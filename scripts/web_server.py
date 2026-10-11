# Copyright (C) 2026 - Joseph M Wilcox
"""Local browser controller for TripoForge; Python standard library only."""
import base64
import binascii
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import threading
import uuid
import webbrowser

import forge
from pipeline.common import PipelineError, read_json, write_json
from pipeline.pricing import estimate, PRICING_DATE
from pipeline.runner import Build, ROOT
from pipeline.tripo import TripoClient

WEB = ROOT / 'web'
VIEWS = ('front', 'left', 'right', 'back')


class LocalHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        # Windows SO_REUSEADDR can let two controllers claim the same port.
        if os.name == 'nt':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def pick_project():
    # A separate process owns Tk's UI thread; only the requested file dialog is visible.
    script = "import tkinter as t; from tkinter import filedialog; r=t.Tk(); r.withdraw(); r.attributes('-topmost',True); print(filedialog.askopenfilename(title='Choose TripoForge project JSON', filetypes=[('Project JSON','*.json')]),flush=True); r.destroy()"
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        raise PipelineError('File picker unavailable; enter the project JSON path directly.')
    return result.stdout.strip()


class Queue:
    def __init__(self, folder=None, settings=None, project=None):
        self.folder = Path(folder or ROOT / 'WebWork').resolve()
        self.settings = str(Path(settings or ROOT / 'config.json').resolve())
        self.project = str(project or ROOT / 'ExampleProjectFiles' / 'MixamoExample' / 'project.json')
        self.path = self.folder / 'queue.json'
        self.lock = threading.RLock()
        self.running = False
        self.worker = None
        self.tasks = read_json(self.path)['tasks'] if self.path.exists() else []
        for task in self.tasks:
            if task['status'] == 'running':
                task.update(status='failed', error='Interrupted run. Verify the previous process stopped, then resume this task.')
        if self.path.exists():
            self.save()

    def save(self):
        write_json(self.path, {'schema': 'tripoforge.web.v1', 'tasks': self.tasks})

    def get(self, task_id):
        for task in self.tasks:
            if task['id'] == task_id and not task.get('removed'):
                return task
        raise PipelineError('Task not found.')

    def add(self, data):
        project = forge.resolve_project(data.get('project', ''))
        name = data.get('name', '').strip()
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', name):
            raise PipelineError('Model name must start with a letter; use letters, numbers and underscores (max 64).')
        images = data.get('images', {})
        if not isinstance(images, dict) or set(images) != set(VIEWS):
            raise PipelineError('Assign all four images before adding a task.')
        topology = data.get('topology')
        if topology not in ('triangle', 'quad') or type(data.get('faces')) is not int:
            raise PipelineError('Choose triangle/quad topology and an integer face count.')
        for flag in ('rig', 'notexture', 'messy', 'subfolders', 'pbr', 'delight', 'smartuv', 'upscale'):
            if type(data.get(flag, False)) is not bool:
                raise PipelineError(f'{flag} must be a boolean.')
        if data.get('smartuv') or data.get('upscale'):
            raise PipelineError('Smart UVs and texture upscaling have no verified public v3 controls yet.')
        if 'rig_spec' in data and data['rig_spec'] not in ('mixamo', 'tripo', 'ue5'):
            raise PipelineError('Choose Mixamo, Tripo or UE5 rig specification.')
        if 'rig_type' in data and data['rig_type'] not in ('biped', 'quadruped', 'hexapod', 'octopod', 'avian', 'serpentine', 'aquatic'):
            raise PipelineError('Choose a supported rig type.')
        decoded = {}
        for view in VIEWS:
            item = images[view]
            if not isinstance(item, dict):
                raise PipelineError(f'{view}: invalid image.')
            suffix = Path(item.get('name', '')).suffix.lower()
            if suffix not in ('.png', '.jpg', '.jpeg'):
                raise PipelineError(f'{view}: use PNG or JPEG.')
            try:
                blob = base64.b64decode(item.get('data', ''), validate=True)
            except (ValueError, binascii.Error, TypeError):
                raise PipelineError(f'{view}: invalid image data.') from None
            if not 0 < len(blob) <= 20 * 1024 * 1024:
                raise PipelineError(f'{view}: image must be nonempty and at most 20 MiB.')
            valid = blob.startswith(b'\x89PNG\r\n\x1a\n') if suffix == '.png' else blob.startswith(b'\xff\xd8\xff')
            if not valid:
                raise PipelineError(f'{view}: file content does not match its image extension.')
            decoded[view] = (suffix, blob)
        task_id = uuid.uuid4().hex
        inputs = self.folder / 'tasks' / task_id / 'input'
        output = Path(data.get('output') or project.parent / 'output').resolve()
        # Browser drag/drop provides image bytes, not their original Windows folder.
        inputs.mkdir(parents=True)
        try:
            return self.store_task(data, project, name, topology, decoded, task_id, inputs, output)
        except Exception:
            # Delete only these newly written uploads; never walk a supplied path.
            for view, (suffix, _) in decoded.items():
                (inputs / f'{name}_{view}{suffix}').unlink(missing_ok=True)
            inputs.rmdir()
            inputs.parent.rmdir()
            raise

    def store_task(self, data, project, name, topology, decoded, task_id, inputs, output):
        for view, (suffix, blob) in decoded.items():
            (inputs / f'{name}_{view}{suffix}').write_bytes(blob)
        job = forge.plan_batch(project, self.settings, inputs, output,
                               subfolders=data.get('subfolders', False), rig=data.get('rig', False),
                               notexture=data.get('notexture', False), topology=topology, faces=data['faces'],
                               pbr=data.get('pbr'), delight=data.get('delight'),
                               rig_spec=data.get('rig_spec'), rig_type=data.get('rig_type'))[0]
        task = {'id': task_id, 'name': name, 'project': str(project), 'config': job['config'],
                'output': str(job['output']), 'messy': data.get('messy', False), 'status': 'pending',
                'created': datetime.now(timezone.utc).isoformat(), 'error': None, 'resume': False}
        with self.lock:
            if any(t['output'].casefold() == task['output'].casefold() and t['status'] in ('pending', 'running')
                   and not t.get('removed') for t in self.tasks):
                raise PipelineError('A queued task already targets that output. Choose a different model name or output folder.')
            self.tasks.append(task)
            self.save()
        return task_id

    def remove(self, task_id):
        with self.lock:
            task = self.get(task_id)
            if task['status'] == 'running':
                raise PipelineError('A running task cannot be removed.')
            task['removed'] = True
            self.save()

    def retry(self, task_id):
        with self.lock:
            task = self.get(task_id)
            if task['status'] != 'failed':
                raise PipelineError('Only failed tasks can be resumed.')
            task.update(status='pending', error=None, resume=True)
            self.save()

    def cost(self, task_id):
        with self.lock:
            return estimate(self.get(task_id)['config'])

    def start(self):
        with self.lock:
            if self.running:
                raise PipelineError('The queue is already running.')
            ids = [t['id'] for t in self.tasks if t['status'] == 'pending' and not t.get('removed')]
            if not ids:
                raise PipelineError('Add a pending task first.')
            self.running = True
            self.worker = threading.Thread(target=self.run, args=(ids,), daemon=True)
            self.worker.start()

    def run(self, ids):
        try:
            for task_id in ids:
                with self.lock:
                    task = self.get(task_id) if any(t['id'] == task_id and not t.get('removed') for t in self.tasks) else None
                    if task is None or task['status'] != 'pending':
                        continue
                    task.update(status='running', error=None)
                    self.save()
                try:
                    work = Path(task['project']).parent / 'Work' / task['name']
                    # Resume uses saved cloud task IDs; never remove locks automatically.
                    resume = task['resume'] and (work / 'current.json').exists()
                    if resume:
                        for lock in (Path(task['project']).parent / '.forge.lock',):
                            self.check_unlocked(lock)
                        saved = (work / read_json(work / 'current.json')['run']).resolve()
                        if not saved.is_relative_to((work / 'runs').resolve()):
                            raise PipelineError('Saved job is outside Work/runs.')
                        self.check_unlocked(saved / '.running')
                    Build(task['config'], self.folder / 'tasks' / task_id / 'preflight').preflight('blender')
                    forge.run_project(task['project'], self.settings, resume=resume, messy=task['messy'],
                                      job_config=task['config'], output_folder=Path(task['output']).parent,
                                      model_name=task['name'])
                    with self.lock:
                        task.update(status='complete', error=None)
                        self.save()
                except Exception as exc:
                    with self.lock:
                        task.update(status='failed', error=str(exc) if isinstance(exc, (PipelineError, OSError)) else type(exc).__name__)
                        self.save()
        finally:
            with self.lock:
                self.running = False

    @staticmethod
    def check_unlocked(path):
        if path.exists():
            # Do not guess PID liveness on Windows, where os.kill(pid, 0) is unsafe.
            raise PipelineError(f'Interrupted build lock remains: {path}. Verify the process stopped and remove this lock before resuming.')

    def state(self):
        with self.lock:
            tasks = []
            for task in self.tasks:
                if task.get('removed'):
                    continue
                cfg = task['config']
                entry = {k: task[k] for k in ('id', 'name', 'project', 'output', 'status', 'error')}
                entry.update(topology='quad' if cfg['tripo']['quad'] else 'triangle',
                             faces=cfg['tripo']['face_limit'], rig=cfg['rig']['mode'] != 'none',
                             texture=cfg['tripo']['texture'], pbr=cfg['tripo']['pbr'],
                             delight=cfg['tripo']['delight'], cost=estimate(cfg))
                entry.update(rig_spec='ue5' if cfg['output_rig']['profile'] == 'ue5' else cfg['rig']['spec'],
                             rig_type=cfg['rig']['type'])
                entry['images'] = {v: f'/api/image/{task["id"]}/{v}' for v in VIEWS}
                if task['status'] == 'running':
                    entry['progress'] = self.progress(task)
                tasks.append(entry)
            return {'running': self.running, 'tasks': tasks, 'pricing_date': PRICING_DATE,
                    'default_project': self.project}

    def progress(self, task):
        try:
            work = Path(task['project']).parent / 'Work' / task['name']
            run = (work / read_json(work / 'current.json')['run']).resolve()
            if not run.is_relative_to((work / 'runs').resolve()):
                return 'Starting'
            stages = read_json(run / 'manifest.json')['stages']
            if stages:
                stage, info = list(stages.items())[-1]
                return f"{stage.replace('_', ' ')} · {info.get('progress', info.get('status', 'running'))}"
        except (OSError, ValueError, KeyError):
            pass
        return 'Starting'


def handler_for(queue, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, data, status=200):
            body = json.dumps(data).encode()
            self.send(body, 'application/json', status)

        def send(self, body, mime, status=200):
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            expected = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != expected:
                self.reply({'error': 'Use the local TripoForge URL.'}, 403)
                return False
            if self.path.startswith('/api/') and self.headers.get('X-TripoForge-Token') != token:
                self.reply({'error': 'Reload the TripoForge page.'}, 403)
                return False
            return True

        def do_GET(self):
            # Thumbnails use an authenticated fetch and a blob URL in the UI.
            if not self.authorized():
                return
            try:
                if self.path == '/':
                    html = (WEB / 'index.html').read_text(encoding='utf-8').replace('__TOKEN__', token)
                    self.send(html.encode(), 'text/html; charset=utf-8')
                elif self.path in ('/app.js', '/style.css'):
                    path = WEB / self.path[1:]
                    self.send(path.read_bytes(), 'text/javascript' if path.suffix == '.js' else 'text/css')
                elif self.path == '/assets/asset-pack-games.png':
                    self.send((WEB / 'assets' / 'asset-pack-games.png').read_bytes(), 'image/png')
                elif self.path == '/api/state':
                    self.reply(queue.state())
                elif re.fullmatch(r'/api/image/[a-f0-9]{32}/(front|left|right|back)', self.path):
                    _, _, _, task_id, view = self.path.split('/')
                    with queue.lock:
                        path = Path(queue.get(task_id)['config']['inputs'][view])
                    self.send(path.read_bytes(), 'image/png' if path.suffix == '.png' else 'image/jpeg')
                else:
                    self.reply({'error': 'Not found.'}, 404)
            except (PipelineError, OSError, ValueError, KeyError) as exc:
                self.reply({'error': str(exc)}, 400)

        def do_POST(self):
            if not self.authorized():
                return
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 112 * 1024 * 1024:
                    raise PipelineError('Request exceeds the four-image upload limit.')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise PipelineError('Request must be a JSON object.')
                if self.path == '/api/add':
                    self.reply({'id': queue.add(data)})
                elif self.path == '/api/remove':
                    queue.remove(data.get('id'))
                    self.reply({'ok': True})
                elif self.path == '/api/retry':
                    queue.retry(data.get('id'))
                    self.reply({'ok': True})
                elif self.path == '/api/cost':
                    self.reply(queue.cost(data.get('id')))
                elif self.path == '/api/run':
                    queue.start()
                    self.reply({'ok': True})
                elif self.path == '/api/pick':
                    self.reply({'project': pick_project()})
                elif self.path in ('/api/project', '/api/balance'):
                    project = forge.resolve_project(data['project']) if data.get('project') else None
                    cfg = forge.project_config(project, queue.settings, validate=False) if project else forge.config.load_settings(queue.settings)
                    if self.path == '/api/balance':
                        self.reply(TripoClient.from_config(cfg).balance())
                    else:
                        tripo = cfg['tripo']
                        quad_supported = not tripo['model'].startswith('P1')
                        topology = data.get('topology', 'quad' if quad_supported else 'triangle')
                        minimum, maximum = 1, 150000 if topology == 'quad' else 2000000
                        if tripo['model'].startswith('P2'):
                            minimum, maximum = 48, 25000 if topology == 'quad' else 50000
                        elif tripo['model'].startswith('P1'):
                            minimum, maximum = 50, 20000
                        elif topology == 'triangle':
                            maximum = 2000000 if tripo['geometry_quality'] == 'detailed' else 1500000 if tripo['model'].startswith('v3.1') else 1000000
                        self.reply({'model': tripo['model'], 'topology': topology,
                                    'faces': tripo['face_limit'], 'minimum': minimum, 'maximum': maximum,
                                    'quad_supported': quad_supported,
                                    'pbr': tripo['pbr'], 'delight': tripo['delight'],
                                    'rig_spec': 'ue5' if cfg['rig']['mode'] == 'ue5' else cfg['rig']['spec'],
                                    'rig_type': cfg['rig']['type'],
                                    'controls': {'pbr': True, 'delight': tripo['texture_version'] == 'v3.5-20260815',
                                                 'smartuv': False, 'upscale': False},
                                    'output': str(project.parent / 'output') if project else ''})
                else:
                    self.reply({'error': 'Not found.'}, 404)
            except (PipelineError, OSError, ValueError, KeyError, TypeError) as exc:
                self.reply({'error': str(exc)}, 400)
    return Handler


def serve(settings=None, port=8768, open_browser=True, project=None):
    token = secrets.token_hex(32)
    # Bind before touching persisted queue state: a second launch must fail harmlessly.
    server = LocalHTTPServer(('127.0.0.1', port), BaseHTTPRequestHandler)
    try:
        queue = Queue(settings=settings, project=project)
        server.RequestHandlerClass = handler_for(queue, token)
    except Exception:
        server.server_close()
        raise
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'TripoForge web interface: {url}', flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--settings', default=str(ROOT / 'config.json'))
    parser.add_argument('--port', type=int, default=8768)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    serve(args.settings, args.port, not args.no_browser)
