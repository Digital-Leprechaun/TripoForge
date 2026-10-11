# Copyright (C) 2026 - Joseph M Wilcox
"""Offline safety/regression tests. No real network, Blender or Unreal calls."""
import copy
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import character_pipeline
from pipeline import config
from pipeline.common import PipelineError, read_json, write_json
from pipeline.runner import Build
from pipeline.tripo import TripoClient


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / 'source.glb'
        self.source.write_bytes(b'glTF' + struct.pack('<II', 2, 12))
        self.cfg = copy.deepcopy(config.DEFAULTS)
        self.cfg['source'] = str(self.source)
        self.output = self.root / 'build'

    def build(self, **kwargs):
        return Build(self.cfg, self.output, **kwargs)


class ConfigTests(Fixture):
    def test_defaults_valid_for_local_source(self):
        config.validate(self.cfg)

    def test_smart_mode_selects_valid_budget(self):
        args = character_pipeline.parser().parse_args(['build', '--source', str(self.source), '--mesh-mode', 'smart'])
        cfg = character_pipeline.configure(args)
        self.assertEqual(cfg['tripo']['model'], 'P2-20260801')
        self.assertEqual(cfg['tripo']['face_limit'], 20000)

    def test_mode_face_limits(self):
        for model, quad, count in [('P1-20260311', False, 20001), ('P1-20260311', True, 1000),
                                    ('P2-20260801', True, 25001), ('P2-20260801', False, 50001)]:
            with self.subTest(model=model, quad=quad):
                cfg = copy.deepcopy(self.cfg)
                cfg['tripo'].update(model=model, quad=quad, face_limit=count)
                with self.assertRaises(PipelineError):
                    config.validate(cfg)

    def test_faces_not_bool_and_height_not_nan(self):
        for field, value in [('face_limit', True), ('face_limit', 1.5)]:
            cfg = copy.deepcopy(self.cfg)
            cfg['tripo'][field] = value
            with self.assertRaises(PipelineError):
                config.validate(cfg)
        self.cfg['blender']['height_m'] = float('nan')
        with self.assertRaises(PipelineError):
            config.validate(self.cfg)

    def test_pbr_requires_texture(self):
        self.cfg['tripo']['texture'] = False
        with self.assertRaises(PipelineError):
            config.validate(self.cfg)

    def test_unknown_keys_and_unknown_rig_modes_rejected(self):
        with self.assertRaises(PipelineError):
            config.merge(config.DEFAULTS, {'api_key': 'do-not-store'})
        self.cfg['rig']['mode'] = 'sometimes'
        with self.assertRaises(PipelineError):
            config.validate(self.cfg)

    def test_all_four_views_required(self):
        self.cfg['source'] = None
        self.cfg['inputs'] = {'front': str(self.source)}
        with self.assertRaises(PipelineError):
            config.validate(self.cfg)


class ClientTests(Fixture):
    def test_post_transport_error_never_retries_or_prints_secret(self):
        opener = Mock(side_effect=URLError('private-secret-in-network-error'))
        client = TripoClient('private-api-key', opener=opener, sleep=Mock())
        with self.assertRaises(PipelineError) as caught:
            client.submit('/generation/multiview-to-model', {})
        self.assertEqual(opener.call_count, 1)
        self.assertNotIn('private', str(caught.exception))

    def test_post_http_error_never_retries(self):
        opener = Mock(side_effect=HTTPError('https://example.test', 503, 'unavailable', {}, None))
        client = TripoClient('secret', opener=opener, sleep=Mock())
        with self.assertRaises(PipelineError):
            client.submit('/task', {})
        self.assertEqual(opener.call_count, 1)

    def test_get_retries_transient_failures(self):
        opener = Mock(side_effect=[URLError('down'), io.BytesIO(b'{"code":0,"data":{"status":"success"}}')])
        sleep = Mock()
        client = TripoClient('secret', opener=opener, sleep=sleep)
        self.assertEqual(client.query('task-1')['status'], 'success')
        self.assertEqual(opener.call_count, 2)
        sleep.assert_called_once()

    def test_malformed_submission_no_retry(self):
        opener = Mock(return_value=io.BytesIO(b'not json'))
        with self.assertRaises(PipelineError):
            TripoClient('secret', opener=opener).submit('/task', {})
        self.assertEqual(opener.call_count, 1)

    def test_missing_task_id_no_retry(self):
        opener = Mock(return_value=io.BytesIO(b'{"code":0,"data":{}}'))
        with self.assertRaises(PipelineError):
            TripoClient('secret', opener=opener).submit('/task', {})
        self.assertEqual(opener.call_count, 1)

    def test_download_never_sends_auth_to_cdn(self):
        opener = Mock(return_value=io.BytesIO(self.source.read_bytes()))
        model = TripoClient('private-secret', opener=opener).download_model(
            {'output': {'model_url': 'https://cdn.example.test/signed.glb?token=private'}}, self.root / 'download')
        request = opener.call_args.args[0]
        self.assertIsNone(request.get_header('Authorization'))
        self.assertEqual(model.read_bytes(), self.source.read_bytes())

    def test_download_rejects_http_credentials_and_truncated_glb(self):
        for url in ['http://cdn.example.test/a', 'https://user:password@cdn.example.test/a']:
            opener = Mock()
            with self.assertRaises(PipelineError):
                TripoClient('secret', opener=opener).download_model({'output': {'model_url': url}}, self.root / 'download')
            opener.assert_not_called()
        opener = Mock(return_value=io.BytesIO(b'glTF' + struct.pack('<II', 2, 100)))
        with self.assertRaises(PipelineError):
            TripoClient('secret', opener=opener).download_model(
                {'output': {'model_url': 'https://cdn.example.test/a'}}, self.root / 'download')

    def test_wait_timeout_only_polls_existing_task(self):
        client = TripoClient('secret', task_timeout=2, sleep=Mock(), clock=Mock(side_effect=[0, 3]))
        client.query = Mock(return_value={'status': 'running'})
        with self.assertRaises(PipelineError):
            client.wait('existing-task')
        client.query.assert_called_once_with('existing-task')


class ResumeTests(Fixture):
    def test_uncertain_submission_blocks_duplicate_on_resume(self):
        client = Mock()
        client.submit.side_effect = PipelineError('response lost')
        build = self.build(client=client)
        with self.assertRaises(PipelineError):
            build.remote('generation', '/task', {'mode': 'smart'})
        resumed_client = Mock()
        resumed = self.build(resume=True, client=resumed_client)
        with self.assertRaisesRegex(PipelineError, 'uncertain submission'):
            resumed.remote('generation', '/task', {'mode': 'smart'})
        resumed_client.submit.assert_not_called()
        self.assertEqual(read_json(build.path)['stages']['generation']['status'], 'submitting')

    def test_known_task_resumes_polling_without_submission(self):
        build = self.build()
        build.manifest['stages']['rig_check'] = {'endpoint': '/check', 'payload': {'input': 'token'},
                                                'task_id': 'known-task', 'status': 'running'}
        build.save()
        client = Mock()
        client.wait.return_value = {'status': 'success', 'output': {'riggable': True}}
        resumed = self.build(resume=True, client=client)
        self.assertEqual(resumed.remote('rig_check', '/check', {'input': 'token'}, model=False), {'riggable': True})
        client.submit.assert_not_called()
        self.assertEqual(client.wait.call_args.args[0], 'known-task')

    def test_cached_artifact_skips_all_network(self):
        build = self.build()
        path = self.output / 'generation' / 'model.glb'
        path.parent.mkdir(parents=True)
        path.write_bytes(self.source.read_bytes())
        entry = {'endpoint': '/task', 'payload': {}, 'status': 'success'}
        build.manifest['stages']['generation'] = entry
        build.record_artifact(entry, path)
        client = Mock()
        resumed = self.build(resume=True, client=client)
        self.assertEqual(resumed.remote('generation', '/task', {}), path)
        self.assertEqual(client.mock_calls, [])

    def test_input_change_invalidates_resume(self):
        build = self.build()
        build.save()
        self.source.write_bytes(b'changed')
        with self.assertRaisesRegex(PipelineError, 'changed'):
            self.build(resume=True)

    def test_artifact_tampering_and_path_escape_rejected(self):
        build = self.build()
        with self.assertRaisesRegex(PipelineError, 'escapes'):
            build.artifact({'artifact': {'path': '../source.glb', 'sha256': 'whatever'}})
        path = self.output / 'model.glb'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'original')
        entry = {}
        build.record_artifact(entry, path)
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(PipelineError, 'changed'):
            build.artifact(entry)

    def test_locked_run_does_not_overwrite_newer_manifest(self):
        original = self.build()
        original.save()
        stale = self.build(resume=True)
        newer = read_json(original.path)
        newer['stages']['generation'] = {'task_id': 'already-billed', 'status': 'queued'}
        write_json(original.path, newer)
        (self.output / '.running').write_text('12345')
        with patch.object(stale, 'preflight'):
            with self.assertRaisesRegex(PipelineError, 'locked'):
                stale.run('tripo')
        self.assertEqual(read_json(original.path), newer)

    def test_raw_key_not_saved_in_manifest(self):
        with patch.dict('os.environ', {'TRIPO_API_KEY': 'private-api-key'}):
            build = self.build()
            build.api()
            build.save()
        self.assertNotIn('private-api-key', build.path.read_text())





class AdditionalSafetyTests(Fixture):
    def test_api_default_redirect_handler_refuses_cross_host(self):
        from pipeline.tripo import NoRedirect
        from urllib.request import Request
        request = Request('https://openapi.tripo3d.ai/v3/tasks/x', headers={'Authorization': 'Bearer secret'})
        self.assertIsNone(NoRedirect().redirect_request(request, None, 302, 'Found', {}, 'https://evil.example.test/'))

    def test_http_error_redacts_echoed_key(self):
        body = io.BytesIO(b'{"message":"invalid credential private-api-key"}')
        opener = Mock(side_effect=HTTPError('https://api.example.test', 401, 'Unauthorized', {}, body))
        with self.assertRaises(PipelineError) as caught:
            TripoClient('private-api-key', opener=opener).submit('/task', {})
        self.assertNotIn('private-api-key', str(caught.exception))
        self.assertEqual(opener.call_count, 1)

    def test_target_references_reach_configuration(self):
        target = self.root / 'target.json'
        write_json(target, {'skeleton': '/Game/Test/Skeleton', 'animation_blueprint': '/Game/Test/AnimBP',
                            'retargeter': '/Game/Test/Retargeter'})
        args = character_pipeline.parser().parse_args(['build', '--source', str(self.source), '--target', str(target)])
        cfg = character_pipeline.configure(args)
        self.assertEqual(cfg['target']['skeleton'], '/Game/Test/Skeleton')
        self.assertEqual(cfg['target']['animation_blueprint'], '/Game/Test/AnimBP')

    def test_download_failure_resume_never_resubmits_completed_task(self):
        client = Mock()
        client.submit.return_value = 'paid-task'
        client.wait.return_value = {'status': 'success', 'output': {'model_url': 'https://cdn.example.test/private'}}
        client.download_model.side_effect = PipelineError('download failed')
        build = self.build(client=client)
        with self.assertRaises(PipelineError):
            build.remote('generation', '/task', {})
        resumed_client = Mock()
        resumed_client.wait.return_value = client.wait.return_value
        model = self.output / 'generation' / 'model.glb'
        model.parent.mkdir(parents=True)
        model.write_bytes(self.source.read_bytes())
        resumed_client.download_model.return_value = model
        resumed = self.build(resume=True, client=resumed_client)
        self.assertEqual(resumed.remote('generation', '/task', {}), model)
        resumed_client.submit.assert_not_called()
        self.assertNotIn('https://cdn.example.test/private', resumed.path.read_text())

    def test_newer_manifest_loaded_after_lock_acquired(self):
        original = self.build()
        original.save()
        stale = self.build(resume=True)
        latest = read_json(original.path)
        latest['external_marker'] = 'must-not-disappear'
        write_json(original.path, latest)
        with patch.object(stale, 'preflight'):
            stale.run('tripo')
        self.assertEqual(read_json(original.path)['external_marker'], 'must-not-disappear')

class DeliveryRegressions(Fixture):
    def test_key_file_is_relative_to_config(self):
        folder = self.root / 'config'
        folder.mkdir()
        path = folder / 'settings.json'
        write_json(path, {'key_file': 'key.txt'})
        self.assertEqual(config.load(path)['key_file'], str(folder / 'key.txt'))

    def test_non_object_api_response_is_controlled_error(self):
        client = TripoClient('secret', opener=lambda *a, **k: io.BytesIO(b'[]'))
        with self.assertRaises(PipelineError):
            client.submit('/generation/multiview-to-model', {})

    def test_fbx_sidecar_is_preserved_and_protected(self):
        source = self.root / 'Hero.fbx'
        source.write_bytes(b'Kaydara FBX Binary')
        sidecar = source.with_suffix('.fbm')
        sidecar.mkdir()
        (sidecar / 'body.png').write_bytes(b'image')
        self.cfg['source'] = str(source)
        build = self.build()
        with patch.object(build, 'preflight'):
            build.run('tripo')
        copied = self.output / 'source' / 'Hero.fbm' / 'body.png'
        self.assertEqual(copied.read_bytes(), b'image')
        copied.write_bytes(b'changed')
        with self.assertRaises(PipelineError):
            build.artifact(build.manifest['stages']['source'])

    def test_engine_warning_resume_does_not_repeat_import(self):
        build = self.build()
        write_json(self.output / 'unreal' / 'report.json', {'status': 'success'})
        build.manifest['stages']['unreal'] = {'status': 'imported_with_engine_errors', 'report': 'unreal/report.json'}
        with patch.object(build, 'process') as process:
            build.unreal(self.source, True)
        process.assert_not_called()


if __name__ == '__main__':
    unittest.main()
