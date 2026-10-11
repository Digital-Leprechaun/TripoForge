# Copyright (C) 2026 - Joseph M Wilcox
"""Offline upload, persistence, worker and HTTP contract checks for the controller."""
import base64
import copy
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import forge
import web_server as web
from pipeline.common import PipelineError, write_json

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII=')


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'project.json'
        self.settings = self.root / 'settings.json'
        write_json(self.project, {'name': 'Example', 'tripo': {'model': 'P2-20260801', 'face_limit': 20000}})
        write_json(self.settings, {})
        self.queue = web.Queue(self.root / 'WebWork', self.settings)
        self.data = {'project': str(self.project), 'name': 'Robot', 'topology': 'quad', 'faces': 12000,
                     'images': {v: {'name': f'{v}.png', 'data': base64.b64encode(PNG).decode()} for v in web.VIEWS}}

    def test_snapshot_persistence_default_output_and_flags(self):
        self.data.update(rig=True, notexture=True, subfolders=True)
        identity = self.queue.add(self.data)
        write_json(self.project, {'name': 'Changed'})
        restored = web.Queue(self.queue.folder, self.settings)
        task = restored.get(identity)
        self.assertEqual(task['config']['tripo']['model'], 'P2-20260801')
        self.assertEqual(Path(task['output']), self.root / 'output' / 'Robot' / 'Robot.glb')
        self.assertFalse((self.root / 'output').exists())
        self.assertFalse(task['config']['tripo']['export_uv'])
        self.assertEqual(task['config']['rig']['mode'], 'force')
        for path in task['config']['inputs'].values():
            self.assertEqual(Path(path).read_bytes(), PNG)
        self.assertNotIn('key_file', json.dumps(restored.state()))

    def test_duplicate_and_invalid_faces_leave_no_orphan_uploads(self):
        self.queue.add(self.data)
        with self.assertRaisesRegex(PipelineError, 'already targets'):
            self.queue.add(self.data)
        self.data.update(name='Other', faces=25001)
        with self.assertRaises(PipelineError):
            self.queue.add(self.data)
        self.assertEqual(len(list((self.queue.folder / 'tasks').iterdir())), 1)

    def test_invalid_images_rejected_before_writing(self):
        for mutate in (lambda d: d['images'].pop('back'),
                       lambda d: d['images']['front'].update(data='bm90LWEtcG5n'),
                       lambda d: d.update(name='../escape')):
            data = copy.deepcopy(self.data)
            mutate(data)
            with self.assertRaises(PipelineError):
                self.queue.add(data)
        self.assertFalse(self.queue.folder.exists())

    def test_cost_is_offline_and_remove_is_persistent(self):
        identity = self.queue.add(self.data)
        with patch.object(web.TripoClient, 'from_config', side_effect=AssertionError('network')):
            self.assertEqual(self.queue.cost(identity)['max'], 125)
        self.queue.remove(identity)
        self.assertEqual(web.Queue(self.queue.folder, self.settings).state()['tasks'], [])

    def test_material_checkbox_overrides_are_snapshotted_and_exposed(self):
        self.data.update(pbr=False, delight=False)
        identity = self.queue.add(self.data)
        restored = web.Queue(self.queue.folder, self.settings)
        task = restored.get(identity)
        self.assertFalse(task['config']['tripo']['pbr'])
        self.assertFalse(task['config']['tripo']['delight'])
        displayed = restored.state()['tasks'][0]
        self.assertFalse(displayed['pbr'])
        self.assertFalse(displayed['delight'])
        self.assertEqual(displayed['cost']['max'], 125)
        self.data.update(name='Untextured', notexture=True, pbr=False, delight=False)
        identity = self.queue.add(self.data)
        self.assertFalse(self.queue.get(identity)['config']['tripo']['texture'])
        self.assertEqual(self.queue.cost(identity)['max'], 105)

    def test_unverified_features_and_nonboolean_materials_are_rejected(self):
        for field, value in [('smartuv', True), ('upscale', True), ('pbr', 'yes'), ('delight', 1)]:
            data = copy.deepcopy(self.data)
            data[field] = value
            with self.assertRaises(PipelineError):
                self.queue.add(data)
        self.assertFalse(self.queue.folder.exists())

    def test_rig_dropdowns_override_project_and_snapshot_converter_selection(self):
        reference = self.root / 'reference.blend'
        reference.write_bytes(b'private reference fixture')
        write_json(self.settings, {'mixamo2ue5': {'reference': str(reference)}})
        write_json(self.project, {'rig': {'mode': 'ue5'}})
        self.data.update(rig=True, rig_spec='tripo', rig_type='quadruped')
        identity = self.queue.add(self.data)
        task = self.queue.get(identity)
        self.assertEqual(task['config']['rig'], {'mode': 'force', 'type': 'quadruped', 'spec': 'tripo'})
        self.assertEqual(task['config']['output_rig']['profile'], 'source')
        self.data.update(name='UE5', rig_spec='ue5', rig_type='biped')
        identity = self.queue.add(self.data)
        task = self.queue.get(identity)
        self.assertEqual(task['config']['rig']['spec'], 'mixamo')
        self.assertEqual(task['config']['output_rig']['profile'], 'ue5')
        restored = web.Queue(self.queue.folder, self.settings)
        self.assertEqual(restored.state()['tasks'][1]['rig_spec'], 'ue5')
        self.assertEqual(restored.state()['tasks'][1]['rig_type'], 'biped')
        self.assertEqual(restored.cost(identity)['max'], 90)
        self.data.update(name='Invalid', rig_type='avian')
        with self.assertRaisesRegex(PipelineError, 'biped'):
            self.queue.add(self.data)

    def test_invalid_rig_dropdown_values_are_rejected_before_upload(self):
        for field, value in [('rig_spec', 'actorcore'), ('rig_type', 'unknown')]:
            data = copy.deepcopy(self.data)
            data[field] = value
            with self.assertRaises(PipelineError):
                self.queue.add(data)
        self.assertFalse(self.queue.folder.exists())

    def test_worker_continues_after_failure_and_retries_saved_job(self):
        first = self.queue.add(self.data)
        self.data['name'] = 'Other'
        second = self.queue.add(self.data)
        def generate(*args, **kwargs):
            if kwargs['model_name'] == 'Robot':
                raise PipelineError('fixture failure')
        with patch.object(web.Build, 'preflight'), patch.object(forge, 'run_project', side_effect=generate) as run:
            self.queue.start()
            self.queue.worker.join(5)
            self.assertEqual(run.call_count, 2)
        self.assertFalse(self.queue.running)
        self.assertEqual(self.queue.get(first)['status'], 'failed')
        self.assertEqual(self.queue.get(second)['status'], 'complete')
        self.queue.retry(first)
        work = self.root / 'Work' / 'Robot'
        write_json(work / 'current.json', {'run': 'runs/fixture'})
        with patch.object(web.Build, 'preflight'), patch.object(forge, 'run_project') as run:
            self.queue.start()
            self.queue.worker.join(5)
            self.assertTrue(run.call_args.kwargs['resume'])
        self.assertEqual(self.queue.get(first)['status'], 'complete')

    def test_restart_running_task_becomes_failed(self):
        identity = self.queue.add(self.data)
        self.queue.get(identity)['status'] = 'running'
        self.queue.save()
        restored = web.Queue(self.queue.folder, self.settings)
        self.assertEqual(restored.get(identity)['status'], 'failed')
        self.assertIn('Interrupted', restored.get(identity)['error'])

    def http(self, path, data=None, authenticated=True, host=None):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        headers = {'Content-Type': 'application/json'}
        if authenticated:
            headers['X-TripoForge-Token'] = 'fixture-token'
        if host:
            headers['Host'] = host
        connection.request('GET' if data is None else 'POST', path,
                           body=None if data is None else json.dumps(data), headers=headers)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def server_start(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), web.handler_for(self.queue, 'fixture-token'))
        worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        worker.start()
        def close():
            self.server.shutdown()
            self.server.server_close()
            worker.join()
        self.addCleanup(close)

    def test_http_auth_upload_image_cost_remove_and_balance(self):
        self.server_start()
        self.assertEqual(self.http('/api/state', authenticated=False)[0], 403)
        self.assertEqual(self.http('/', host='attacker.example')[0], 403)
        status, html = self.http('/')
        self.assertEqual(status, 200)
        self.assertIn(b'fixture-token', html)
        self.assertIn(b'/assets/asset-pack-games.png', html)
        self.assertEqual(self.http('/assets/asset-pack-games.png'),
                         (200, (web.WEB / 'assets' / 'asset-pack-games.png').read_bytes()))
        status, body = self.http('/api/add', self.data)
        self.assertEqual(status, 200)
        identity = json.loads(body)['id']
        self.assertEqual(self.http(f'/api/image/{identity}/front'), (200, PNG))
        self.assertEqual(json.loads(self.http('/api/cost', {'id': identity})[1])['max'], 125)
        with patch.object(web.TripoClient, 'from_config') as client:
            client.return_value.balance.return_value = {'balance': 2065, 'frozen': 0}
            self.assertEqual(json.loads(self.http('/api/balance', {'project': str(self.project)})[1])['balance'], 2065)
        self.assertEqual(self.http('/api/remove', {'id': identity})[0], 200)
        self.assertEqual(json.loads(self.http('/api/state')[1])['tasks'], [])

    def test_project_limits_follow_model_and_selected_topology(self):
        self.server_start()
        for model, topology, maximum, quad in [('P2-20260801', 'triangle', 50000, True),
                                               ('P2-20260801', 'quad', 25000, True),
                                               ('P1-20260311', 'triangle', 20000, False),
                                               ('v3.1-20260211', 'quad', 150000, True),
                                               ('v3.1-20260211', 'triangle', 2000000, True)]:
            write_json(self.project, {'tripo': {'model': model}})
            status, body = self.http('/api/project', {'project': str(self.project), 'topology': topology})
            self.assertEqual(status, 200)
            result = json.loads(body)
            self.assertEqual(result['maximum'], maximum)
            self.assertEqual(result['quad_supported'], quad)

    def test_web_defaults_to_quads_with_p1_fallback_and_material_capabilities(self):
        self.server_start()
        result = json.loads(self.http('/api/project', {'project': str(self.project)})[1])
        self.assertEqual(result['topology'], 'quad')
        self.assertEqual(result['maximum'], 25000)
        self.assertTrue(result['controls']['pbr'])
        self.assertTrue(result['controls']['delight'])
        self.assertFalse(result['controls']['smartuv'])
        self.assertFalse(result['controls']['upscale'])
        write_json(self.project, {'tripo': {'model': 'P1-20260311', 'texture_version': 'v3.0-20250812', 'delight': False}})
        result = json.loads(self.http('/api/project', {'project': str(self.project)})[1])
        self.assertEqual(result['topology'], 'triangle')
        self.assertFalse(result['controls']['delight'])

    def test_web_cli_launches_without_inputs(self):
        with patch.object(web, 'serve') as serve:
            self.assertEqual(forge.main(['--web', '--settings', str(self.settings)]), 0)
            serve.assert_called_once_with(settings=str(self.settings), project=None)

    def test_controller_port_cannot_be_claimed_by_a_second_instance(self):
        server = web.LocalHTTPServer(('127.0.0.1', 0), web.handler_for(self.queue, 'test'))
        self.addCleanup(server.server_close)
        with self.assertRaises(OSError):
            web.LocalHTTPServer(('127.0.0.1', server.server_port), web.handler_for(self.queue, 'test'))


if __name__ == '__main__':
    unittest.main()
