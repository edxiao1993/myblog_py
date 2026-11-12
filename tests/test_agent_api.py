import json
import tempfile
import os
import signal
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import time
import unittest
from pathlib import Path
from flask import Flask
from database import db
from models.user import User
from routes import register_blueprints
from utils.jwt import generate_access_token
from utils.i18n import init_i18n


class AgentAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        fake_pi = root / 'pi'
        fake_pi.write_text('''#!/usr/bin/env python3
import json, sys, time
from pathlib import Path
for line in sys.stdin:
    cmd = json.loads(line)
    if cmd['type'] == 'abort':
        print(json.dumps({'type': 'status', 'message': 'Stopped.'}), flush=True)
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
    if cmd['type'] != 'prompt':
        continue
    msg = cmd.get('message', '')
    if msg == 'slow-run':
        time.sleep(1.5)
    def emit(event):
        print(json.dumps(event), flush=True)
    emit({'type': 'response', 'id': cmd['id'], 'success': True})
    if msg == 'multi-turn':
        emit({'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'Let me check. '}})
        emit({'type': 'message_end', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Let me check. '}, {'type': 'toolCall', 'id': 't1', 'name': 'read', 'arguments': {}}]}})
    emit({'type': 'tool_execution_start', 'toolCallId': 't1', 'toolName': 'read', 'args': {}})
    emit({'type': 'tool_execution_end', 'toolCallId': 't1', 'toolName': 'read', 'result': {}})
    if msg == 'multi-turn':
        emit({'type': 'message_end', 'message': {'role': 'toolResult', 'toolCallId': 't1', 'content': 'notes'}})
        emit({'type': 'message_start', 'message': {'role': 'assistant'}})
    if msg != 'final-only':
        emit({'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'Hello '}})
        emit({'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'world!'}})
    if msg == 'partial-cancel':
        continue
    answer = {'role': 'assistant', 'content': 'Hello world!'}
    if msg == 'provider-error':
        answer['errorMessage'] = 'Sample provider error'
    emit({'type': 'message_end', 'message': answer})
    emit({'type': 'agent_settled'})
''')
        fake_pi.chmod(0o700)
        prompt = root / 'prompt.txt'
        prompt.write_text('Be helpful.')

        self.app = Flask(__name__)
        self.app.config.update(
            SECRET_KEY='test-secret',
            JWT_SECRET_KEY='test-jwt-secret',
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            PI_EXECUTABLE=str(fake_pi),
            PI_DATA_DIR=str(root / 'data'),
            PI_WORKSPACE=str(root / 'workspace'),
            PI_SYSTEM_PROMPT_FILE=str(prompt),
            PI_TIMEOUT=5
        )
        init_i18n(self.app)
        db.init_app(self.app)
        register_blueprints(self.app)

        with self.app.app_context():
            db.create_all()
            self.user1 = User(username='kevin', email='kevin@example.com')
            self.user1.set_password('pass123')
            self.user2 = User(username='other', email='other@example.com')
            self.user2.set_password('pass123')
            db.session.add(self.user1)
            db.session.add(self.user2)
            db.session.commit()
            self.user1_id = self.user1.id
            self.user2_id = self.user2.id

        self.client = self.app.test_client()
        with self.app.app_context():
            self.token1 = generate_access_token(self.user1_id)
            self.token2 = generate_access_token(self.user2_id)

        self.auth1 = {'Authorization': f'Bearer {self.token1}'}
        self.auth2 = {'Authorization': f'Bearer {self.token2}'}

    def tearDown(self):
        from services.agent_service import _ACTIVE_RUNS, _SERVICE_LOCK
        with _SERVICE_LOCK:
            threads = [item['thread'] for item in list(_ACTIVE_RUNS.values()) if 'thread' in item]
        for t in threads:
            t.join(timeout=2.0)
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
        try:
            self.temp.cleanup()
        except OSError:
            pass

    def test_unauthorized_access(self):
        # Missing token
        res = self.client.get('/api/agent/config')
        self.assertEqual(res.status_code, 401)
        data = res.get_json()
        self.assertEqual(data['error'], 'unauthorized')
        self.assertTrue(data['retryable'])
        self.assertTrue(data['request_id'])

        # Invalid token format
        res = self.client.get('/api/agent/config', headers={'Authorization': 'BadToken'})
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.get_json()['error'], 'unauthorized')

    def test_get_config(self):
        res = self.client.get('/api/agent/config', headers=self.auth1)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['agent']['available'])
        self.assertEqual(data['agent']['name'], 'PI')
        self.assertTrue(len(data['models']) > 0)
        self.assertEqual(data['models'][0]['id'], 'test/reasoner')
        self.assertEqual(data['defaults']['working_directory_id'], 'default')
        self.assertEqual(data['editable_settings'], ['model_id', 'thinking_level'])
        self.assertEqual(data['limits']['max_message_characters'], 20000)

    def test_create_session_and_idempotency(self):
        # Create with defaults
        res = self.client.post('/api/agent/sessions', headers=self.auth1, json={
            'client_request_id': 'req-create-1'
        })
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertIn('session', data)
        session = data['session']
        sid = session['id']
        self.assertEqual(len(sid), 32)
        self.assertEqual(session['title'], 'New conversation')
        self.assertEqual(session['settings']['working_directory_id'], 'default')
        self.assertIsNone(session['active_run'])

        # Idempotent retry with same client_request_id
        res2 = self.client.post('/api/agent/sessions', headers=self.auth1, json={
            'client_request_id': 'req-create-1'
        })
        self.assertEqual(res2.status_code, 201)
        self.assertEqual(res2.get_json()['session']['id'], sid)

        # Idempotency conflict with different settings
        res3 = self.client.post('/api/agent/sessions', headers=self.auth1, json={
            'client_request_id': 'req-create-1',
            'settings': {
                'model_id': 'test/reasoner',
                'thinking_level': 'high',
                'working_directory_id': 'default'
            }
        })
        self.assertEqual(res3.status_code, 409)
        self.assertEqual(res3.get_json()['error'], 'idempotency_conflict')

    def test_create_session_with_explicit_settings_and_validation(self):
        # Invalid settings (missing required fields)
        res = self.client.post('/api/agent/sessions', headers=self.auth1, json={
            'settings': {'model_id': 'test/reasoner'}
        })
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.get_json()['error'], 'invalid_settings')

        # Invalid working directory
        res = self.client.post('/api/agent/sessions', headers=self.auth1, json={
            'settings': {
                'model_id': 'test/reasoner',
                'thinking_level': 'low',
                'working_directory_id': 'invalid-dir'
            }
        })
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.get_json()['error'], 'invalid_settings')

        # Valid settings
        res = self.client.post('/api/agent/sessions', headers=self.auth1, json={
            'settings': {
                'model_id': 'test/reasoner',
                'thinking_level': 'low',
                'working_directory_id': 'default'
            }
        })
        self.assertEqual(res.status_code, 201)
        session = res.get_json()['session']
        self.assertEqual(session['settings']['model_id'], 'test/reasoner')
        self.assertEqual(session['settings']['thinking_level'], 'low')

    def test_list_and_get_sessions(self):
        # Create 2 sessions
        s1 = self.client.post('/api/agent/sessions', headers=self.auth1, json={}).get_json()['session']
        time.sleep(0.01)
        s2 = self.client.post('/api/agent/sessions', headers=self.auth1, json={}).get_json()['session']

        # List
        res = self.client.get('/api/agent/sessions?limit=10', headers=self.auth1)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        ids = [s['id'] for s in data['sessions']]
        self.assertIn(s1['id'], ids)
        self.assertIn(s2['id'], ids)
        # Most recent first
        self.assertEqual(ids[0], s2['id'])

        # Get session detail
        res = self.client.get(f"/api/agent/sessions/{s1['id']}", headers=self.auth1)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()['session']['id'], s1['id'])

        # User isolation: user2 cannot see user1's session
        res = self.client.get(f"/api/agent/sessions/{s1['id']}", headers=self.auth2)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.get_json()['error'], 'session_not_found')

    def test_update_settings(self):
        s = self.client.post('/api/agent/sessions', headers=self.auth1, json={}).get_json()['session']
        sid = s['id']

        # Update
        res = self.client.patch(f"/api/agent/sessions/{sid}/settings", headers=self.auth1, json={
            'model_id': 'test/reasoner',
            'thinking_level': 'high',
            'working_directory_id': 'default'
        })
        self.assertEqual(res.status_code, 200)
        updated = res.get_json()['session']
        self.assertEqual(updated['settings']['model_id'], 'test/reasoner')
        self.assertEqual(updated['settings']['thinking_level'], 'high')

        # Invalid update
        res = self.client.patch(f"/api/agent/sessions/{sid}/settings", headers=self.auth1, json={
            'model_id': 'invalid-model/none',
            'thinking_level': 'high',
            'working_directory_id': 'default'
        })
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.get_json()['error'], 'invalid_settings')

    def test_submit_run_streaming_and_messages(self):
        s = self.client.post('/api/agent/sessions', headers=self.auth1, json={}).get_json()['session']
        sid = s['id']

        # Submit run
        res = self.client.post(f"/api/agent/sessions/{sid}/runs", headers=self.auth1, json={
            'client_request_id': 'run-req-1',
            'message': {'content': 'Hello Pi'}
        })
        self.assertEqual(res.status_code, 202)
        run_data = res.get_json()['run']
        run_id = run_data['id']
        self.assertEqual(run_data['status'], 'queued')
        self.assertTrue(run_data['user_message_id'])
        self.assertTrue(run_data['assistant_message_id'])

        # Idempotent submit
        res_idem = self.client.post(f"/api/agent/sessions/{sid}/runs", headers=self.auth1, json={
            'client_request_id': 'run-req-1',
            'message': {'content': 'Hello Pi'}
        })
        self.assertEqual(res_idem.status_code, 202)
        self.assertEqual(res_idem.get_json()['run']['id'], run_id)

        # Idempotent conflict
        res_conflict = self.client.post(f"/api/agent/sessions/{sid}/runs", headers=self.auth1, json={
            'client_request_id': 'run-req-1',
            'message': {'content': 'Different message'}
        })
        self.assertEqual(res_conflict.status_code, 409)
        self.assertEqual(res_conflict.get_json()['error'], 'idempotency_conflict')

        # Stream events
        res_stream = self.client.get(f"/api/agent/sessions/{sid}/runs/{run_id}/events", headers=self.auth1)
        self.assertEqual(res_stream.status_code, 200)
        self.assertEqual(res_stream.mimetype, 'text/event-stream')
        stream_text = res_stream.get_data(as_text=True)

        self.assertIn('event: status', stream_text)
        self.assertIn('event: message_delta', stream_text)
        self.assertIn('event: run_finished', stream_text)
        self.assertIn('"delta": "Hello "', stream_text)
        self.assertIn('"delta": "world!"', stream_text)
        self.assertIn('"status": "completed"', stream_text)

        # Check messages history
        time.sleep(0.1)
        res_msgs = self.client.get(f"/api/agent/sessions/{sid}/messages", headers=self.auth1)
        self.assertEqual(res_msgs.status_code, 200)
        messages = res_msgs.get_json()['messages']
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]['role'], 'user')
        self.assertEqual(messages[0]['content'], 'Hello Pi')
        self.assertEqual(messages[0]['status'], 'completed')
        self.assertEqual(messages[1]['role'], 'assistant')
        self.assertEqual(messages[1]['content'], 'Hello world!')
        self.assertEqual(messages[1]['status'], 'completed')

    def test_run_message_validation(self):
        s = self.client.post('/api/agent/sessions', headers=self.auth1, json={}).get_json()['session']
        sid = s['id']

        # Empty content
        res = self.client.post(f"/api/agent/sessions/{sid}/runs", headers=self.auth1, json={
            'message': {'content': '   '}
        })
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.get_json()['error'], 'invalid_message')

        # Oversized content
        res = self.client.post(f"/api/agent/sessions/{sid}/runs", headers=self.auth1, json={
            'message': {'content': 'a' * 20001}
        })
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.get_json()['error'], 'invalid_message')

    def test_cancel_run(self):
        s = self.client.post('/api/agent/sessions', headers=self.auth1, json={}).get_json()['session']
        sid = s['id']

        # Start a slow run
        res = self.client.post(f"/api/agent/sessions/{sid}/runs", headers=self.auth1, json={
            'message': {'content': 'slow-run'}
        })
        self.assertEqual(res.status_code, 202)
        run_id = res.get_json()['run']['id']

        # Cancel it
        res_cancel = self.client.post(f"/api/agent/sessions/{sid}/runs/{run_id}/cancel", headers=self.auth1)
        self.assertEqual(res_cancel.status_code, 200)
        self.assertIn(res_cancel.get_json()['run']['status'], ('cancelled', 'running'))

        # Wait for cancellation to settle
        time.sleep(0.5)
        res_cancel2 = self.client.post(f"/api/agent/sessions/{sid}/runs/{run_id}/cancel", headers=self.auth1)
        self.assertEqual(res_cancel2.status_code, 200)

    def test_last_event_id_replay(self):
        s = self.client.post('/api/agent/sessions', headers=self.auth1, json={}).get_json()['session']
        sid = s['id']

        res = self.client.post(f"/api/agent/sessions/{sid}/runs", headers=self.auth1, json={
            'message': {'content': 'Hello replay'}
        })
        run_id = res.get_json()['run']['id']
        time.sleep(0.3)

        # Reconnect with Last-Event-ID = 2 (skipping event 1 and 2)
        res_stream = self.client.get(
            f"/api/agent/sessions/{sid}/runs/{run_id}/events",
            headers={**self.auth1, 'Last-Event-ID': '2'}
        )
        self.assertEqual(res_stream.status_code, 200)
        stream_text = res_stream.get_data(as_text=True)
        self.assertNotIn('id: 1\n', stream_text)
        self.assertNotIn('id: 2\n', stream_text)
        self.assertIn('event: run_finished', stream_text)

    def test_message_pagination_with_before(self):
        s = self.client.post('/api/agent/sessions', headers=self.auth1, json={}).get_json()['session']
        sid = s['id']

        # Submit 2 runs sequentially
        self.client.post(f"/api/agent/sessions/{sid}/runs", headers=self.auth1, json={'message': {'content': 'msg 1'}})
        time.sleep(0.3)
        self.client.post(f"/api/agent/sessions/{sid}/runs", headers=self.auth1, json={'message': {'content': 'msg 2'}})
        time.sleep(0.3)

        # Total 4 messages: user1, asst1, user2, asst2
        res = self.client.get(f"/api/agent/sessions/{sid}/messages?limit=2", headers=self.auth1)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(len(data['messages']), 2)
        self.assertEqual(data['messages'][0]['content'], 'msg 2')
        self.assertEqual(data['messages'][1]['content'], 'Hello world!')
        self.assertTrue(data['next_before'])

        # Load older page with before
        before_id = data['next_before']
        res_older = self.client.get(f"/api/agent/sessions/{sid}/messages?limit=2&before={before_id}", headers=self.auth1)
        self.assertEqual(res_older.status_code, 200)
        older_data = res_older.get_json()
        self.assertEqual(len(older_data['messages']), 2)
        self.assertEqual(older_data['messages'][0]['content'], 'msg 1')
        self.assertIsNone(older_data['next_before'])

    def test_concurrency_and_active_run_in_detail(self):
        s = self.client.post('/api/agent/sessions', headers=self.auth1, json={}).get_json()['session']
        sid = s['id']

        # Start a slow run
        res = self.client.post(f"/api/agent/sessions/{sid}/runs", headers=self.auth1, json={
            'message': {'content': 'slow-run'}
        })
        self.assertEqual(res.status_code, 202)
        run_id = res.get_json()['run']['id']

        # Detail should reflect active_run
        res_detail = self.client.get(f"/api/agent/sessions/{sid}", headers=self.auth1)
        self.assertEqual(res_detail.status_code, 200)
        active = res_detail.get_json()['session']['active_run']
        self.assertIsNotNone(active)
        self.assertEqual(active['id'], run_id)

        # Trying to update settings while run is active should fail with 409
        res_settings = self.client.patch(f"/api/agent/sessions/{sid}/settings", headers=self.auth1, json={
            'model_id': 'test/reasoner',
            'thinking_level': 'high',
            'working_directory_id': 'default'
        })
        self.assertEqual(res_settings.status_code, 409)
        self.assertEqual(res_settings.get_json()['error'], 'run_active')

        # Submitting second run should fail with 409 run_active
        res_second = self.client.post(f"/api/agent/sessions/{sid}/runs", headers=self.auth1, json={
            'message': {'content': 'Another message'}
        })
        self.assertEqual(res_second.status_code, 409)
        self.assertEqual(res_second.get_json()['error'], 'run_active')

    def completed_run(self, content):
        sid = self.client.post('/api/agent/sessions', headers=self.auth1, json={}).json['session']['id']
        run = self.client.post(f'/api/agent/sessions/{sid}/runs', headers=self.auth1,
            json={'message': {'content': content}}).json['run']
        stream = self.client.get(f"/api/agent/sessions/{sid}/runs/{run['id']}/events", headers=self.auth1).get_data(as_text=True)
        records = [json.loads(line[6:]) for line in stream.splitlines() if line.startswith('data: ')]
        return sid, run, records[-1]

    def test_final_only_reply_is_returned_in_completion(self):
        _, _, finished = self.completed_run('final-only')
        self.assertEqual(finished['message']['content'], 'Hello world!')

    def test_tool_turn_has_unique_ids_and_authoritative_final_message(self):
        sid, run, finished = self.completed_run('multi-turn')
        messages = self.client.get(f'/api/agent/sessions/{sid}/messages', headers=self.auth1).json['messages']
        ids = [item['id'] for item in messages]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(messages[-1]['id'], run['assistant_message_id'])
        self.assertEqual(messages[-1]['status'], 'completed')
        self.assertEqual(finished['message']['content'], 'Hello world!')

    def test_invalid_pagination_is_a_structured_client_error(self):
        sid = self.client.post('/api/agent/sessions', headers=self.auth1, json={}).json['session']['id']
        for suffix in ['?limit=nope', '?limit=-1', '?limit=0']:
            for path in ['/api/agent/sessions', f'/api/agent/sessions/{sid}/messages']:
                with self.subTest(path=path+suffix):
                    response = self.client.get(path+suffix, headers=self.auth1)
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(response.json['error'], 'invalid_request')

    def test_stream_errors_are_validated_before_response_iteration(self):
        response = self.client.get('/api/agent/sessions/'+'f'*32+'/runs/run-missing/events', headers=self.auth1)
        self.assertEqual(response.status_code, 404)
        sid = self.client.post('/api/agent/sessions', headers=self.auth1, json={}).json['session']['id']
        response = self.client.get(f'/api/agent/sessions/{sid}/runs/run-missing/events', headers=self.auth1)
        self.assertEqual(response.status_code, 404)
        from services.pi_service import PiService
        with self.app.app_context():
            folder = PiService(self.app.config).folder(SimpleNamespace(id=self.user1_id, username='kevin'), sid)
        run_id = 'run-' + 'e'*32
        directory = folder / 'runs' / run_id
        directory.mkdir(parents=True)
        (directory / 'meta.json').write_text(json.dumps({'status': 'completed', 'completed_at': '2000-01-01T00:00:00Z'}))
        response = self.client.get(f'/api/agent/sessions/{sid}/runs/{run_id}/events', headers=self.auth1)
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json['error'], 'event_history_expired')

    def test_concurrent_session_retries_return_one_session(self):
        from services.agent_service import AgentService
        service = AgentService(self.app.config)
        user = SimpleNamespace(id=self.user1_id, username='kevin')
        original = service.pi_service.create
        entered = threading.Event()
        def slow_create(*args, **kwargs):
            entered.set()
            time.sleep(.1)
            return original(*args, **kwargs)
        with patch.object(service.pi_service, 'create', side_effect=slow_create):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(service.create_session, user, 'same-key', None)
                self.assertTrue(entered.wait(2))
                second = pool.submit(service.create_session, user, 'same-key', None)
                ids = [item.result()['session']['id'] for item in [first, second]]
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(len(service.pi_service.list(user)), 1)

    def test_cross_worker_stream_tails_new_records_before_completion(self):
        from services.agent_service import AgentService
        service = AgentService(self.app.config)
        user = SimpleNamespace(id=self.user1_id, username='kevin')
        sid = service.create_session(user)['session']['id']
        folder = service.pi_service.folder(user, sid)
        run_id = 'run-' + 'd'*32
        directory = folder / 'runs' / run_id
        directory.mkdir(parents=True)
        (directory / 'meta.json').write_text(json.dumps({'status': 'running'}))
        # Writer never calls a process-local emit/subscriber function.
        events = directory / 'events.jsonl'
        written = threading.Event()
        def write():
            time.sleep(.05)
            with events.open('a') as handle:
                handle.write(json.dumps({'id': 1, 'event': 'message_delta', 'data': {'delta': 'live'}})+'\n')
            written.set()
            time.sleep(.15)
            with events.open('a') as handle:
                handle.write(json.dumps({'id': 2, 'event': 'run_finished', 'data': {}})+'\n')
            (directory / 'meta.json').write_text(json.dumps({'status': 'completed'}))
        worker = threading.Thread(target=write)
        worker.start()
        stream = service.stream_run_events(user, sid, run_id)
        try:
            first = next(stream)
            self.assertIn('event: message_delta', first)
            self.assertIn('live', first)
            self.assertIn('event: run_finished', ''.join(stream))
        finally:
            stream.close()
            worker.join(2)

    def test_completed_stream_last_id_returns_without_heartbeat(self):
        sid, run, _ = self.completed_run('Hello')
        from services.agent_service import AgentService
        service = AgentService(self.app.config)
        user = SimpleNamespace(id=self.user1_id, username='kevin')
        directory = service.pi_service.folder(user, sid) / 'runs' / run['id']
        records = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()]
        self.assertEqual(list(service.stream_run_events(user, sid, run['id'], records[-1]['id'])), [])

    def test_tail_waits_for_complete_json_record(self):
        from services.agent_service import AgentService
        service = AgentService(self.app.config)
        user = SimpleNamespace(id=self.user1_id, username='kevin')
        sid = service.create_session(user)['session']['id']
        directory = service.pi_service.folder(user, sid) / 'runs' / ('run-' + 'e'*32)
        directory.mkdir(parents=True)
        (directory / 'meta.json').write_text(json.dumps({'status': 'running'}))
        text = json.dumps({'id': 1, 'event': 'run_finished', 'data': {}}) + '\n'
        (directory / 'events.jsonl').write_text(text[:10])
        def finish():
            time.sleep(.1)
            with (directory / 'events.jsonl').open('a') as handle:
                handle.write(text[10:])
        writer = threading.Thread(target=finish)
        writer.start()
        try:
            frames = list(service.stream_run_events(user, sid, directory.name))
            self.assertEqual(len(frames), 1)
            self.assertIn('event: run_finished', frames[0])
        finally:
            writer.join(2)

    def test_worker_exit_stops_pi_and_finalizes_the_run(self):
        fake = Path(self.app.config['PI_EXECUTABLE'])
        fake.write_text('#!' + sys.executable + '\n' + '''import json, os, sys, time
from pathlib import Path
for line in sys.stdin:
    if json.loads(line).get('type') == 'prompt':
        Path(__file__).with_suffix('.pid').write_text(str(os.getpid()))
        time.sleep(30)
''')
        config = {key: value for key, value in self.app.config.items() if key.startswith('PI_')}
        child = '''import json, sys, time
from pathlib import Path
from types import SimpleNamespace
from services.agent_service import AgentService
config=json.loads(sys.argv[1])
service=AgentService(config)
user=SimpleNamespace(id=1,username='exit-test')
sid=service.create_session(user)['session']['id']
service.submit_run(user,sid,None,{'content':'wait'})
marker=Path(config['PI_EXECUTABLE']).with_suffix('.pid')
for _ in range(100):
    if marker.exists(): break
    time.sleep(.02)
assert marker.exists()
'''
        result = subprocess.run([sys.executable, '-c', child, json.dumps(config)],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15)
        marker = fake.with_suffix('.pid')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(marker.exists())
        pid = int(marker.read_text())
        try:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        finally:
            try: os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError: pass
        root = Path(config['PI_DATA_DIR']) / 'users' / 'exit-test'
        metas = list(root.glob('*/runs/*/meta.json'))
        self.assertEqual(len(metas), 1)
        self.assertEqual(json.loads(metas[0].read_text())['status'], 'cancelled')
        self.assertEqual(list(root.glob('*/active_run.json')), [])

    def test_run_retries_are_serialized_and_queued_state_is_stable(self):
        from services.agent_service import AgentService
        service = AgentService(self.app.config)
        user = SimpleNamespace(id=self.user1_id, username='kevin')
        sid = service.create_session(user)['session']['id']
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(service.submit_run, user, sid, 'same-run-key', {'content': 'slow-run'}) for _ in range(2)]
            runs = [item.result()['run'] for item in futures]
        self.assertEqual(runs[0]['id'], runs[1]['id'])
        self.assertEqual(runs[0]['status'], 'queued')
        self.assertEqual(runs[1]['status'], 'queued')

    def test_invalid_idempotency_key_returns_client_error(self):
        for key in [[], {}, '', 123]:
            response = self.client.post('/api/agent/sessions', headers=self.auth1, json={'client_request_id': key})
            self.assertEqual(response.status_code, 422)

    def test_gunicorn_reload_cancels_a_run_with_an_open_sse_connection(self):
        import http.client
        import socket
        root = Path(self.temp.name)
        fake = root / 'reload-pi'
        fake.write_text('#!' + sys.executable + '\n' + '''import json, os, sys
from pathlib import Path
for line in sys.stdin:
    command = json.loads(line)
    if command['type'] == 'prompt':
        Path(__file__).with_suffix('.pid').write_text(str(os.getpid()))
    elif command['type'] == 'abort':
        print(json.dumps({'type':'status','message':'Stopped.'}), flush=True)
        print(json.dumps({'type':'agent_settled'}), flush=True)
''')
        fake.chmod(0o700)
        shared = {key: value for key, value in self.app.config.items() if key.startswith('PI_')}
        shared.update(PI_EXECUTABLE=str(fake), PI_DATA_DIR=str(root/'shared-data'), PI_TIMEOUT=60)
        (root/'agent_reload_probe.py').write_text('from tests.test_agent_api import AgentAPITests\n'
            'fixture=AgentAPITests()\nfixture.setUp()\napp=fixture.app\napp.config.update(' + repr(shared) + ')\n')
        project = Path(__file__).resolve().parents[1]
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        server = subprocess.Popen([sys.executable, '-m', 'gunicorn', '--config', str(project/'gunicorn.conf.py'),
            '--workers', '2', '--threads', '2', '--graceful-timeout', '15', '--bind', f'127.0.0.1:{port}', 'agent_reload_probe:app'],
            cwd=project, env={**os.environ, 'PYTHONPATH': str(root)+os.pathsep+str(project), 'LOG_DIR': str(root/'reload-logs')},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        connections = []
        def request(method, path, data=None):
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=8)
            connections.append(connection)
            headers = {**self.auth1, 'Content-Type': 'application/json'}
            connection.request(method, path, body=json.dumps(data) if data is not None else None, headers=headers)
            return connection.getresponse()
        try:
            for _ in range(120):
                try:
                    response = request('GET', '/api/agent/sessions')
                    if response.status == 200:
                        response.read()
                        break
                except OSError:
                    time.sleep(.03)
            else:
                self.fail('Gunicorn did not start')
            sid = json.loads(request('POST', '/api/agent/sessions', {}).read())['session']['id']
            run_id = json.loads(request('POST', f'/api/agent/sessions/{sid}/runs', {'message': {'content': 'wait'}}).read())['run']['id']
            stream = request('GET', f'/api/agent/sessions/{sid}/runs/{run_id}/events')
            self.assertEqual(stream.status, 200)
            marker = fake.with_suffix('.pid')
            for _ in range(100):
                if marker.exists(): break
                time.sleep(.02)
            self.assertTrue(marker.exists())
            server.send_signal(signal.SIGHUP)
            output = stream.read().decode()
            self.assertIn('event: run_finished', output)
            self.assertIn('"status": "cancelled"', output)
            with self.assertRaises(ProcessLookupError):
                os.kill(int(marker.read_text()), 0)
        finally:
            for connection in connections:
                connection.close()
            server.terminate()
            try: server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=3)
            marker = fake.with_suffix('.pid')
            if marker.exists():
                try: os.killpg(int(marker.read_text()), signal.SIGTERM)
                except ProcessLookupError: pass

    def test_failed_response_has_consistent_history_status(self):
        sid, run, finished = self.completed_run('provider-error')
        self.assertEqual(finished['status'], 'failed')
        history = self.client.get(f'/api/agent/sessions/{sid}/messages', headers=self.auth1).json['messages']
        self.assertEqual(history[-1]['id'], run['assistant_message_id'])
        self.assertEqual(history[-1]['status'], 'failed')
        self.assertEqual(history[-1]['content'], 'Hello world!')

    def test_cancellation_persists_partial_text_without_message_end(self):
        sid = self.client.post('/api/agent/sessions', headers=self.auth1, json={}).json['session']['id']
        run = self.client.post(f'/api/agent/sessions/{sid}/runs', headers=self.auth1,
            json={'message': {'content': 'partial-cancel'}}).json['run']
        response = self.client.get(f"/api/agent/sessions/{sid}/runs/{run['id']}/events", headers=self.auth1, buffered=False)
        iterator = iter(response.response)
        try:
            for frame in iterator:
                if b'event: message_delta' in frame:
                    break
            self.client.post(f"/api/agent/sessions/{sid}/runs/{run['id']}/cancel", headers=self.auth1)
            remaining = b''.join(iterator).decode()
            self.assertIn('event: run_finished', remaining)
            self.assertIn('"status": "cancelled"', remaining)
        finally:
            response.close()
        history = self.client.get(f'/api/agent/sessions/{sid}/messages', headers=self.auth1).json['messages']
        self.assertEqual(history[-1]['id'], run['assistant_message_id'])
        self.assertEqual(history[-1]['status'], 'cancelled')
        self.assertEqual(history[-1]['content'], 'Hello world!')

    def test_api_completion_keeps_transcript_append_only_for_memory_checkpoints(self):
        sid, _, _ = self.completed_run('multi-turn')
        from services.pi_service import PiService
        folder = PiService(self.app.config).folder(SimpleNamespace(id=self.user1_id, username='kevin'), sid)
        path = folder / 'messages.jsonl'
        before, inode = path.read_bytes(), path.stat().st_ino
        response = self.client.post(f'/api/agent/sessions/{sid}/runs', headers=self.auth1,
            json={'message': {'content': 'second turn'}})
        self.assertEqual(response.status_code, 202)
        run_id = response.json['run']['id']
        self.client.get(f'/api/agent/sessions/{sid}/runs/{run_id}/events', headers=self.auth1).get_data()
        self.assertEqual(path.stat().st_ino, inode)
        self.assertTrue(path.read_bytes().startswith(before))

    def test_finished_event_allows_immediate_next_run(self):
        from services.agent_service import _atomic_write
        def delayed_final_meta(path, text):
            data = json.loads(text)
            if path.name == 'meta.json' and data.get('status') == 'completed':
                time.sleep(.3)
            return _atomic_write(path, text)
        with patch('services.agent_service._atomic_write', side_effect=delayed_final_meta):
            sid, _, _ = self.completed_run('hello')
            response = self.client.post(f'/api/agent/sessions/{sid}/runs', headers=self.auth1,
                json={'message': {'content': 'next'}})
            self.assertEqual(response.status_code, 202)
            run_id = response.json['run']['id']
            self.client.get(f'/api/agent/sessions/{sid}/runs/{run_id}/events', headers=self.auth1).get_data()


if __name__ == '__main__':
    unittest.main()
