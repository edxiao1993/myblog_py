import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from utils.i18n import init_i18n
from flask import Flask
from flask_login import LoginManager, UserMixin
from routes.pi_agent import bp
from services.pi_service import BusyError, PiService


class User(UserMixin):
    def __init__(self, uid):
        self.id = int(uid)
        self.username = 'kevin' if self.id == 1 else 'other'


class PiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        fake = root / 'pi'
        fake.write_text('''#!/usr/bin/env python3
import json, sys
from pathlib import Path
Path('rpc-test-command.json').write_text(json.dumps(sys.argv))
for line in sys.stdin:
    cmd = json.loads(line)
    if cmd['type'] == 'abort':
        print(json.dumps({'type': 'agent_settled'}), flush=True)
        continue
    replies = {
        'get_available_models': {'models': [{'id': 'reasoner', 'provider': 'test', 'name': 'Test reasoner', 'reasoning': True}]},
        'get_available_thinking_levels': {'levels': ['off', 'low', 'high']},
        'get_state': {'model': {'id': 'reasoner', 'provider': 'test'}, 'thinkingLevel': 'low'},
    }
    if cmd['type'] in replies:
        print(json.dumps({'id': cmd['id'], 'type': 'response', 'success': True, 'data': replies[cmd['type']]}), flush=True)
        continue
    if cmd['type'] != 'prompt': continue
    Path('last-prompt.json').write_text(json.dumps(cmd))
    if cmd.get('message') == 'simulate-upstream-error':
        print('HTTP 502 secret-stderr-value', file=sys.stderr, flush=True)
        print(json.dumps({'type': 'response', 'id': 'prompt', 'success': False, 'error': 'HTTP 502 private-provider-body'}), flush=True)
        continue
    if cmd.get('message') == 'wait': continue
    def emit(event): print(json.dumps(event), flush=True)
    if cmd.get('streamingBehavior') == 'steer':
        emit({'type': 'agent_settled'})  # Completion already in flight when append arrives.
    emit({'type': 'response', 'id': cmd['id'], 'success': True})
    emit({'type': 'tool_execution_start', 'toolCallId': 't', 'toolName': 'read', 'args': {'path': 'demo.txt'}})
    emit({'type': 'tool_execution_end', 'toolCallId': 't', 'toolName': 'read', 'result': {'content': [{'type':'text','text':'hello'}]}})
    emit({'type': 'message_end', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'hello'}]}})
    emit({'type': 'agent_end'})
    emit({'type': 'agent_settled'})
''')
        fake.chmod(0o700)
        prompt = root / 'prompt.txt'
        prompt.write_text('Be helpful.')
        self.app = Flask(__name__, template_folder=str(Path('templates').resolve()))
        self.app.config.update(SECRET_KEY='test', TESTING=True,
            PI_EXECUTABLE=str(fake), PI_DATA_DIR=str(root / 'data'), PI_WORKSPACE=str(root),
            PI_SYSTEM_PROMPT_FILE=str(prompt), PI_MODEL='', PI_TIMEOUT=5)
        init_i18n(self.app)
        login = LoginManager(self.app)
        login.user_loader(lambda uid: User(uid))
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()
        self.login(1)
        self.headers = {'X-CSRF-Token': 'token'}

    def tearDown(self):
        self.temp.cleanup()

    def login(self, uid):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(uid)
            session['_fresh'] = True
            session['pi_csrf'] = 'token'

    def create(self):
        response = self.client.post('/api/pi/sessions', json={}, headers=self.headers)
        self.assertEqual(response.status_code, 201)
        return response.json['id']

    def test_agent_lifecycle_logs_metadata_without_chat_contents(self):
        sid = self.create()
        private_message = 'private-chat-password-never-log-this'
        with self.assertLogs('pi.agent', level='INFO') as logs:
            response = self.client.post(f'/api/pi/sessions/{sid}/messages',
                json={'message': private_message}, headers=self.headers)
            _ = response.data
        output = '\n'.join(logs.output)
        self.assertIn('run.started', output)
        self.assertIn('process.started', output)
        self.assertIn('tool.started', output)
        self.assertIn('tool.finished', output)
        self.assertIn('run.completed', output)
        self.assertIn('stream.closed', output)
        self.assertNotIn(private_message, output)
        self.assertNotIn('demo.txt', output)
        self.assertNotIn('"text": "hello"', output)
        self.assertIn('X-Request-ID', response.headers)

    def test_upstream_error_status_logged_without_provider_body(self):
        sid = self.create()
        with self.assertLogs('pi.agent', level='INFO') as logs:
            response = self.client.post(f'/api/pi/sessions/{sid}/messages',
                json={'message': 'simulate-upstream-error'}, headers=self.headers)
            _ = response.data
        output = '\n'.join(logs.output)
        self.assertIn('"http_status":502', output)
        self.assertIn('run.failed', output)
        self.assertNotIn('private-provider-body', output)
        self.assertNotIn('secret-stderr-value', output)

    def test_agent_start_failure_is_logged_without_exception_secrets(self):
        sid = self.create()
        self.app.config['PI_EXECUTABLE'] = '/private/secret-token-value/pi'
        with self.assertLogs('pi.agent', level='INFO') as logs:
            response = self.client.post(f'/api/pi/sessions/{sid}/messages',
                json={'message': 'private chat'}, headers=self.headers)
            _ = response.data
        output = '\n'.join(logs.output)
        self.assertIn('run.failed', output)
        self.assertIn('FileNotFoundError', output)
        self.assertNotIn('secret-token-value', output)
        self.assertNotIn('private chat', output)

    def test_stream_history_and_lock_release(self):
        sid = self.create()
        response = self.client.post(f'/api/pi/sessions/{sid}/messages',
            json={'message': 'Read demo.txt'}, headers=self.headers)
        events = [json.loads(frame[6:]) for frame in response.data.decode().strip().split('\n\n')]
        self.assertIn('tool_execution_end', [event['type'] for event in events])
        self.assertEqual(events[-1]['type'], 'done')
        history = self.client.get(f'/api/pi/sessions/{sid}').json
        self.assertEqual([msg['role'] for msg in history], ['user', 'assistant'])
        agent = PiService(self.app.config)
        lock = agent.acquire(agent.folder(User(1), sid))
        lock.close()

    def test_stop_timeout_and_disconnect_release_lock(self):
        sid = self.create()
        agent = PiService(self.app.config)
        folder = agent.folder(User(1), sid)
        lock = agent.acquire(folder)
        (folder / 'stop').touch()
        events = list(agent.run(folder, 'wait', lock))
        self.assertEqual(events[-1]['type'], 'done')
        self.assertIn('status', [event['type'] for event in events])
        agent.config['PI_TIMEOUT'] = 0
        events = list(agent.run(folder, 'wait', agent.acquire(folder)))
        self.assertEqual(events[-1]['type'], 'done')
        agent.config['PI_TIMEOUT'] = 5
        stream = agent.run(folder, 'hello', agent.acquire(folder))
        next(stream)
        stream.close()
        lock = agent.acquire(folder)
        lock.close()

    def test_page_and_archive(self):
        for endpoint in ['main.index', 'moments.moments',
                         'ai_sessions.sessions_list', 'auth.logout']:
            self.app.add_url_rule('/test/' + endpoint, endpoint, lambda: '')
        response = self.client.get('/pi-agent')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Be helpful.', response.data)
        sid = self.create()
        response = self.client.post(f'/api/pi/sessions/{sid}/messages',
            json={'message': 'hello'}, headers=self.headers)
        _ = response.data
        from services.duckdb_service import close_duckdb_connection, get_session_by_id
        with patch.dict('os.environ', {'DUCKDB_PATH': str(Path(self.temp.name) / 'archive.duckdb'),
                                      'FLASK_DEBUG': 'False', 'FLASK_ENV': 'production'}):
            try:
                result = self.client.post(f'/api/pi/sessions/{sid}/archive', headers=self.headers)
                self.assertEqual(result.status_code, 200)
                with self.app.app_context():
                    saved, error = get_session_by_id(1, result.json['url'].split('/')[-1])
                    self.assertIsNone(error)
                    self.assertEqual(len(saved['messages']), 3)
            finally:
                close_duckdb_connection()

    def test_login_named_directories_and_pi_defaults(self):
        sid = self.create()
        response = self.client.post(f'/api/pi/sessions/{sid}/messages',
            json={'message': 'hello'}, headers=self.headers)
        _ = response.data
        root = Path(self.app.config['PI_DATA_DIR'])
        self.assertTrue((root / 'users' / 'kevin' / sid / 'messages.jsonl').exists())
        workspace = Path(self.app.config['PI_WORKSPACE']) / 'kevin'
        args = json.loads((workspace / 'rpc-test-command.json').read_text())
        self.assertNotIn('--provider', args)
        self.assertNotIn('--model', args)
        self.assertNotIn('--api-key', args)
        self.app.config.update(PI_PROVIDER='example-provider', PI_MODEL='example-model')
        response = self.client.post(f'/api/pi/sessions/{sid}/messages',
            json={'message': 'hello again'}, headers=self.headers)
        _ = response.data
        args = json.loads((workspace / 'rpc-test-command.json').read_text())
        self.assertEqual(args[args.index('--provider') + 1], 'example-provider')
        self.assertEqual(args[args.index('--model') + 1], 'example-model')
        self.login(2)
        other_sid = self.create()
        self.assertTrue((root / 'users' / 'other' / other_sid).is_dir())

    def test_legacy_history_migration_and_safe_login_names(self):
        agent = PiService(self.app.config)
        sid = 'a' * 32
        legacy = agent.root / '1' / sid
        legacy.mkdir(parents=True)
        (legacy / 'metadata.json').write_text(json.dumps({'id': sid, 'title': 'Old chat', 'created': 1}))
        (legacy / 'messages.jsonl').write_text(json.dumps({'role': 'user', 'content': 'old message'}) + '\n')
        self.assertEqual(agent.list(User(1))[0]['id'], sid)
        folder = agent.folder(User(1), sid)
        self.assertEqual(folder.parent.name, 'kevin')
        self.assertEqual(agent.history(folder)[0]['content'], 'old message')
        self.assertFalse(legacy.exists())
        for username in ['../someone', '..', '.', '/tmp', 'a%2Fb', '张三']:
            user = User(3)
            user.username = username
            path = agent.user_root(user)
            self.assertEqual(path.parent, agent.root / 'users')
            self.assertNotIn('/', path.name)
        self.assertNotEqual(agent.login_folder('a/b'), agent.login_folder('a%2Fb'))

    def test_access_csrf_and_ownership(self):
        self.assertEqual(self.client.post('/api/pi/sessions', json={}).status_code, 403)
        sid = self.create()
        self.login(2)
        self.assertEqual(self.client.get('/api/pi/sessions').status_code, 200)
        self.assertEqual(self.client.get(f'/api/pi/sessions/{sid}').status_code, 404)
        self.assertEqual(self.client.post(f'/api/pi/sessions/{sid}/stop', headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get('/api/pi/sessions').json, [])
        self.create()  # Any authenticated account may start its own conversation.
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get('/api/pi/sessions').status_code, 401)

    def test_append_reaches_active_rpc_and_preserves_history(self):
        sid = self.create()
        agent = PiService(self.app.config)
        folder = agent.folder(User(1), sid)
        ready = threading.Event()
        events = []

        def run():
            for event in agent.run(folder, 'wait', agent.acquire(folder)):
                events.append(event)
                if event['type'] == 'heartbeat':
                    ready.set()

        worker = threading.Thread(target=run)
        worker.start()
        try:
            self.assertTrue(ready.wait(3))
            response = self.client.post(f'/api/pi/sessions/{sid}/append',
                json={'message': 'Use Chinese instead'}, headers=self.headers)
            self.assertEqual(response.status_code, 202)
            worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(events[-1]['type'], 'done')
            self.assertFalse(any(item['type'] == 'error' for item in events))
            command = json.loads((agent.workspace('kevin') / 'last-prompt.json').read_text())
            self.assertEqual(command['streamingBehavior'], 'steer')
            self.assertEqual(command['message'], 'Use Chinese instead')
            history = agent.history(folder)
            self.assertEqual([m['content'] for m in history if m['role'] == 'user'],
                             ['wait', 'Use Chinese instead'])
            self.assertFalse((folder / 'inbox.active').exists())
        finally:
            (folder / 'stop').touch()
            worker.join(6)

    def test_append_validates_owner_csrf_message_and_active_run(self):
        sid = self.create()
        route = f'/api/pi/sessions/{sid}/append'
        self.assertEqual(self.client.post(route, json={'message': 'hello'}).status_code, 403)
        for message in (' ', 'x' * 32001, None):
            self.assertEqual(self.client.post(route, json={'message': message},
                                             headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post(route, json={'message': 'hello'},
                                         headers=self.headers).status_code, 409)
        self.login(2)
        self.assertEqual(self.client.post(route, json={'message': 'hello'},
                                         headers=self.headers).status_code, 404)

    def test_busy_validation_and_failed_start(self):
        sid = self.create()
        path = f'/api/pi/sessions/{sid}/messages'
        self.assertEqual(self.client.post(path, json={'message': ' '}, headers=self.headers).status_code, 400)
        agent = PiService(self.app.config)
        lock = agent.acquire(agent.folder(User(1), sid))
        self.assertEqual(self.client.post(path, json={'message': 'hello'}, headers=self.headers).status_code, 409)
        lock.close()
        self.app.config['PI_EXECUTABLE'] = '/nonexistent/pi'
        response = self.client.post(path, json={'message': 'hello'}, headers=self.headers)
        self.assertIn('"type": "error"', response.data.decode())
        lock = agent.acquire(agent.folder(User(1), sid))
        lock.close()


    def test_options_are_from_pi(self):
        response = self.client.get('/api/pi/options?model=test/reasoner')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['levels'], ['off', 'low', 'high'])
        self.assertEqual(response.json['models'][0]['id'], 'reasoner')
        self.assertIn('office', response.json['prompts'])

    def test_history_restores_settings_and_resumes_native_session(self):
        response = self.client.post('/api/pi/sessions', json={
            'model': 'test/reasoner', 'thinking': 'high', 'prompt': 'office'}, headers=self.headers)
        self.assertEqual(response.status_code, 201)
        sid = response.json['id']
        for text in ['first request', 'continue the same task']:
            stream = self.client.post(f'/api/pi/sessions/{sid}/messages',
                json={'message': text}, headers=self.headers)
            self.assertIn('"type": "done"', stream.data.decode())
        detail = self.client.get(f'/api/pi/sessions/{sid}/detail').json
        self.assertEqual(detail['session']['settings']['prompt'], 'office')
        self.assertEqual(detail['session']['settings']['thinking'], 'high')
        self.assertEqual(len(detail['messages']), 4)
        agent = PiService(self.app.config)
        folder = agent.folder(User(1), sid)
        args = json.loads((agent.workspace('kevin') / 'rpc-test-command.json').read_text())
        self.assertEqual(args[args.index('--session') + 1], str(folder / 'session.jsonl'))
        self.assertEqual(args[args.index('--model') + 1], 'reasoner')
        self.assertEqual(args[args.index('--thinking') + 1], 'high')
        # Prompt changes require a new conversation after the first message.
        self.assertEqual(self.client.post(f'/api/pi/sessions/{sid}/settings',
            json={'prompt': 'pi'}, headers=self.headers).status_code, 400)
        result = self.client.post(f'/api/pi/sessions/{sid}/settings',
            json={'model': 'test/reasoner', 'thinking': 'low'}, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['settings']['thinking'], 'low')

    def test_upload_download_and_file_ownership(self):
        from io import BytesIO
        sid = self.create()
        endpoint = f'/api/pi/sessions/{sid}/files'
        response = self.client.post(endpoint, data={'files': (BytesIO(b'office data'), '../report.csv')},
            headers=self.headers, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 201)
        name = response.json['files'][0]['path']
        download = self.client.get(endpoint + '/' + name)
        self.assertEqual(download.data, b'office data')
        self.assertIn('attachment;', download.headers['Content-Disposition'])
        download.close()
        stream = self.client.post(f'/api/pi/sessions/{sid}/messages',
            json={'message': 'Summarize this', 'attachments': [name]}, headers=self.headers)
        _ = stream.data
        detail = self.client.get(f'/api/pi/sessions/{sid}/detail').json
        self.assertEqual(detail['messages'][0]['attachments'], [name])
        self.login(2)
        self.assertEqual(self.client.get(endpoint).status_code, 404)
        self.assertEqual(self.client.get(endpoint + '/' + name).status_code, 404)
        self.assertEqual(self.client.get(f'/api/pi/sessions/{sid}/detail').status_code, 404)

    def test_custom_prompts_are_private_persistent_and_snapshotted(self):
        response = self.client.post('/api/pi/prompts', json={
            'name': '我的工作 <report>', 'text': '用中文帮我整理每周报告。'}, headers=self.headers)
        self.assertEqual(response.status_code, 201)
        key = response.json['id']
        prompts = self.client.get('/api/pi/prompts').json
        self.assertEqual(prompts[key]['name'], '我的工作 <report>')
        response = self.client.post('/api/pi/sessions', json={'prompt': key}, headers=self.headers)
        self.assertEqual(response.status_code, 201)
        sid = response.json['id']
        self.assertEqual(self.client.get(f'/api/pi/sessions/{sid}/detail').json['session']['prompt_text'],
                         '用中文帮我整理每周报告。')
        result = self.client.post(f'/api/pi/sessions/{sid}/messages', json={'message': 'hello'}, headers=self.headers)
        self.assertIn('"type": "done"', result.data.decode())
        agent = PiService(self.app.config)
        command = json.loads((agent.workspace('kevin') / 'rpc-test-command.json').read_text())
        self.assertIn('用中文帮我整理每周报告。', command[command.index('--append-system-prompt') + 1])
        self.assertIn(key, agent.prompts(User(1)))
        self.assertEqual(len(agent.list(User(1))), 1)
        self.login(2)
        self.assertNotIn(key, self.client.get('/api/pi/prompts').json)
        self.assertEqual(self.client.post('/api/pi/sessions', json={'prompt': key},
                                         headers=self.headers).status_code, 400)

    def test_custom_prompt_validation_and_csrf(self):
        route = '/api/pi/prompts'
        self.assertEqual(self.client.post(route, json={'name': 'Test', 'text': 'Help'}).status_code, 403)
        for data in ({}, [], {'name': ' ', 'text': 'Help'}, {'name': 'x'*81, 'text': 'Help'},
                     {'name': 'Test', 'text': ''}, {'name': 'Test', 'text': 'x'*32001}):
            self.assertEqual(self.client.post(route, json=data, headers=self.headers).status_code, 400)

    def test_general_prompt_is_default_and_existing_prompts_are_preserved(self):
        sid = self.create()
        agent = PiService(self.app.config)
        folder = agent.folder(User(1), sid)
        metadata = agent.metadata(folder)
        self.assertEqual(metadata['settings']['prompt'], 'general')
        self.assertIn('everyday tasks', metadata['prompt_text'])
        self.assertIn('casual conversation', metadata['prompt_text'])
        self.assertEqual(self.client.get('/api/pi/options').json['prompts']['general']['name'],
                         'General assistant')
        metadata.pop('settings')
        metadata.pop('prompt_text')
        agent.write_metadata(folder, metadata)
        self.assertEqual(agent.metadata(folder)['settings']['prompt'], 'coding')
        self.assertEqual(agent.metadata(folder)['prompt_text'], 'Be helpful.')

    def test_settings_validation_defaults_and_prompt_snapshot(self):
        response = self.client.post('/api/pi/sessions', json={
            'model': 'test/reasoner', 'thinking': 'high', 'prompt': 'office'}, headers=self.headers)
        sid = response.json['id']
        route = f'/api/pi/sessions/{sid}/settings'
        self.assertEqual(self.client.post(route, json={'thinking': 'max'}, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post(route, json={'model': 'invalid'}, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post(route, json={'model': 'test/missing'}, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post(route, json={'prompt': 'missing'}, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post(route, json=['invalid'], headers=self.headers).status_code, 400)
        result = self.client.post(route, json={'model': '', 'thinking': ''}, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['settings']['default_model'], 'test/reasoner')
        self.assertEqual(result.json['settings']['default_thinking'], 'low')
        result = self.client.post(f'/api/pi/sessions/{sid}/messages', json={'message': 'hello'}, headers=self.headers)
        _ = result.data
        saved = self.client.get(f'/api/pi/sessions/{sid}/detail').json
        self.assertIn('office assistant', saved['session']['prompt_text'])
        # Resume through a fresh service instance, rather than process-local state.
        agent = PiService(self.app.config)
        self.assertEqual(len(agent.history(agent.folder(User(1), sid))), 2)
        self.login(2)
        self.assertEqual(self.client.post(route, json={'thinking': 'low'}, headers=self.headers).status_code, 404)

    def test_unicode_generated_download_complete_and_range(self):
        from urllib.parse import quote
        sid = self.create()
        agent = PiService(self.app.config)
        root = agent.conversation_files(agent.folder(User(1), sid))
        filename = '辩论赛-全球正在变暖.pptx'
        payload = b'PK' + bytes(range(256)) * 1024
        (root / 'outputs' / filename).write_bytes(payload)
        url = f'/api/pi/sessions/{sid}/files/outputs/' + quote(filename)
        with self.client.get(url) as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, payload)
            self.assertEqual(int(response.headers['Content-Length']), len(payload))
            self.assertIn("filename*=UTF-8''", response.headers['Content-Disposition'])
            self.assertIn(quote(filename), response.headers['Content-Disposition'])
        with self.client.get(url, headers={'Range': 'bytes=0-99'}) as response:
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.data, payload[:100])
        self.login(2)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_duplicate_uploads_outputs_and_symlink_directory(self):
        from io import BytesIO
        sid = self.create()
        endpoint = f'/api/pi/sessions/{sid}/files'
        names = []
        for body in [b'first', b'second']:
            result = self.client.post(endpoint, data={'files': (BytesIO(body), 'same.txt')},
                headers=self.headers, content_type='multipart/form-data')
            self.assertEqual(result.status_code, 201)
            names.append(result.json['files'][0]['path'])
        self.assertNotEqual(*names)
        with self.client.get(endpoint + '/' + names[0]) as download:
            self.assertEqual(download.data, b'first')
        agent = PiService(self.app.config)
        root = agent.conversation_files(agent.folder(User(1), sid))
        (root / 'outputs' / 'report.txt').write_text('generated report')
        result = self.client.get(endpoint + '/outputs/report.txt')
        self.assertEqual(result.data, b'generated report')
        self.assertEqual(result.headers['X-Content-Type-Options'], 'nosniff')
        result.close()
        self.assertEqual(len(self.client.get(endpoint).json), 3)
        (root / 'outputs' / 'report.txt').unlink()
        (root / 'outputs').rmdir()
        (root / 'outputs').symlink_to(Path(self.temp.name), target_is_directory=True)
        result = self.client.post(endpoint, data={'files': (BytesIO(b'no'), 'bad.txt')},
            headers=self.headers, content_type='multipart/form-data')
        self.assertEqual(result.status_code, 404)

    def test_default_upload_limit_accepts_above_25mb_and_rejects_above_200mb(self):
        from io import BytesIO
        sid = self.create()
        endpoint = f'/api/pi/sessions/{sid}/files'
        size = 26 * 1024 * 1024
        response = self.client.post(endpoint, data={'files': (BytesIO(b'x' * size), 'large.bin')},
            headers=self.headers, content_type='multipart/form-data')
        self.addCleanup(response.request.input_stream.close)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json['files'][0]['size'], size)
        response = self.client.post(endpoint, data=b'',
            environ_overrides={'CONTENT_LENGTH': str(200 * 1024 * 1024 + 1)}, headers=self.headers)
        self.assertEqual(response.status_code, 413)
        self.assertIn('200 MB', response.json['error'])

    def test_file_limits_traversal_and_symlinks(self):
        from io import BytesIO
        sid = self.create()
        endpoint = f'/api/pi/sessions/{sid}/files'
        self.app.config['PI_MAX_UPLOAD_BYTES'] = 10
        result = self.client.post(endpoint, data={'files': (BytesIO(b'x' * 11), 'large.txt')},
            headers=self.headers, content_type='multipart/form-data')
        self.assertEqual(result.status_code, 413)
        agent = PiService(self.app.config)
        workspace = agent.conversation_files(agent.folder(User(1), sid))
        (workspace / 'outputs' / 'secret.txt').symlink_to(Path(self.temp.name) / 'prompt.txt')
        self.assertEqual(self.client.get(endpoint + '/outputs/secret.txt').status_code, 404)
        self.assertEqual(self.client.get(endpoint).json, [])
        result = self.client.post(f'/api/pi/sessions/{sid}/messages',
            json={'message': 'read', 'attachments': ['../../prompt.txt']}, headers=self.headers)
        self.assertEqual(result.status_code, 400)

    def test_pi_agent_page_rendering_and_mobile_layout(self):
        for endpoint in ['main.index', 'moments.moments',
                         'ai_sessions.sessions_list', 'auth.logout', 'auth.login', 'auth.register']:
            if endpoint not in self.app.view_functions:
                self.app.add_url_rule('/test/' + endpoint, endpoint, lambda: '')
        response = self.client.get('/pi-agent')
        self.assertEqual(response.status_code, 200)
        html = response.data.decode('utf-8')
        self.assertIn('id="pi-top-bar"', html)
        self.assertIn('id="pi-sidebar-drawer"', html)
        self.assertIn('offcanvas-md', html)
        self.assertIn('id="pi-settings-collapse"', html)
        self.assertIn('id="pi-files-collapse"', html)
        self.assertIn('id="pi-messages"', html)
        self.assertIn('id="pi-form"', html)
        self.assertIn('id="pi-input"', html)
        self.assertIn('id="pi-send"', html)
        self.assertIn('id="pi-status"', html)
        self.assertIn('id="pi-app"', html)
        self.assertIn('id="pi-toggle-tools"', html)
        self.assertIn('id="pi-sidebar-toggle"', html)
        self.assertIn('id="pi-sidebar-hide-desktop"', html)
        self.assertIn('id="pi-sidebar-show-desktop"', html)
        self.assertIn('pi-desktop-sidebar-hidden', html)
        self.assertIn('hide-tools', html)
        self.assertIn('⌘+Enter / Ctrl+Enter to send', html)
        from html.parser import HTMLParser
        class Elements(HTMLParser):
            def __init__(self):
                super().__init__()
                self.elements = {}
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if 'id' in attrs:
                    self.elements[attrs['id']] = attrs
        parser = Elements()
        parser.feed(html)
        self.assertIn('hide-tools', parser.elements['pi-app']['class'])
        self.assertIn('pi-desktop-sidebar-hidden', parser.elements['pi-app']['class'])
        self.assertNotIn('d-none', parser.elements['pi-top-bar']['class'])
        self.assertIn('#pi-messages > * { flex-shrink: 0; }', html)
        self.assertNotIn('checked', parser.elements['pi-toggle-tools'])
        self.assertNotIn('checked', parser.elements['pi-settings-toggle-tools'])
        self.assertIn('hidden', parser.elements['pi-conversation-info'])
        self.assertNotIn('pi-suggestion-btn', html)
        self.assertNotIn('pi-empty', html)



if __name__ == '__main__':
    unittest.main()
