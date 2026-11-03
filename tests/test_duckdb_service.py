import os
import tempfile
import unittest
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from routes.ai_api import bp
from services import duckdb_service as db


class DuckDBTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, FLASK_ENV='production', FLASK_DEBUG='False',
                              DUCKDB_PATH=str(Path(self.temp.name) / 'archive.duckdb'))
        self.env.start()
        db.close_duckdb_connection()
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.app.register_blueprint(bp, url_prefix='/api/ai')
        self.auth = patch('utils.jwt.verify_access_token', return_value=SimpleNamespace(id=1))
        self.auth.start()
        self.client = self.app.test_client()
        self.headers = {'Authorization': 'Bearer test'}
        self.sid = db.create_session(1, {'session_id': 'session'})[0]['session_id']

    def tearDown(self):
        db.close_duckdb_connection()
        self.auth.stop()
        self.env.stop()
        self.temp.cleanup()

    def post(self, suffix, payload):
        return self.client.post('/api/ai/sessions' + suffix, json=payload, headers=self.headers)

    def test_failed_batch_is_atomic(self):
        response = self.post('/session/messages', {'messages': [
            {'message_id': 'same', 'content': 'first'},
            {'message_id': 'same', 'content': 'different'}]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(db.get_session_by_id(1, self.sid)[0]['messages'], [])

    def test_invalid_payloads(self):
        for payload in (['invalid'], {'messages': 'invalid'},
                        {'messages': [{'content': 'valid'}, 'invalid']},
                        {'messages': [{'role': 'invalid', 'content': 'text'}]},
                        {'messages': [{'created_at': 'not a timestamp'}]}):
            with self.subTest(payload=payload):
                self.assertEqual(self.post('/session/messages', payload).status_code, 400)
        self.assertEqual(self.post('', ['invalid']).status_code, 400)
        self.assertEqual(db.get_session_by_id(1, self.sid)[0]['messages'], [])

    def test_pagination_validation(self):
        for suffix in ('?limit=-1', '?offset=-1', '?limit=abc', '?limit=999999999999999999999999',
                       '/search?q=text&limit=-1'):
            self.assertEqual(self.client.get('/api/ai/sessions' + suffix,
                                            headers=self.headers).status_code, 400)

    def test_retries_and_conflicts(self):
        payload = {'messages': [{'message_id': 'm1', 'content': 'hello'}]}
        first = self.post('/session/messages', payload)
        second = self.post('/session/messages', payload)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json['messages'], second.json['messages'])
        mixed = self.post('/session/messages', {'messages': payload['messages'] + [{'message_id': 'm2', 'content': 'next'}]})
        self.assertEqual([m['seq'] for m in mixed.json['messages']], [0, 1])
        self.assertEqual(self.post('/session/messages', {'messages': [{'message_id': 'm1', 'content': 'changed'}]}).status_code, 409)
        self.assertEqual(self.post('', {'session_id': 'session'}).status_code, 201)
        self.assertEqual(self.post('', {'session_id': 'session', 'title': 'changed'}).status_code, 409)

    def test_retry_preserves_timestamp_and_normalizes_timezone(self):
        payload = [{'message_id': 'timestamped', 'created_at': '2026-11-03T12:00:00+08:00',
                    'content': '中文 <script> stays unchanged'}]
        first, _ = db.append_messages(1, self.sid, payload)
        self.assertEqual(first[0]['created_at'], '2026-11-03 04:00:00')
        before = db.get_session_by_id(1, self.sid)[0]
        with patch.object(db, '_utcnow', return_value='2026-12-01 00:00:00'):
            self.assertEqual(db.append_messages(1, self.sid, payload)[0], first)
        self.assertEqual(db.get_session_by_id(1, self.sid)[0]['updated_at'], before['updated_at'])

    def test_archive_creation_rolls_back_on_insert_failure(self):
        with self.assertRaises(db.ArchiveError):
            db.create_session(1, {'session_id': 'snapshot'}, messages=[
                {'message_id': 'same', 'content': 'a'}, {'message_id': 'same', 'content': 'b'}])
        self.assertIsNone(db.get_session_by_id(1, 'snapshot')[0])
        self.assertEqual(db.search_messages(1, 'a'), [])

    def test_delete_rolls_back_both_tables_on_failure(self):
        db.append_messages(1, self.sid, [{'content': 'private'}])
        original = db._connection

        class FailingDelete:
            def __init__(self, conn):
                self.conn = conn

            def execute(self, sql, params):
                if sql.startswith('DELETE FROM ai_sessions'):
                    raise RuntimeError('simulated database failure')
                return self.conn.execute(sql, params)

        @contextmanager
        def failing_connection():
            with original() as conn:
                yield FailingDelete(conn)

        with patch.object(db, '_connection', failing_connection):
            with self.assertRaises(RuntimeError):
                db.delete_session(1, self.sid)
        self.assertEqual(len(db.get_session_by_id(1, self.sid)[0]['messages']), 1)

    def test_dev_memory_database_shared_and_reset(self):
        with patch.dict(os.environ, FLASK_ENV='development', FLASK_DEBUG='True'):
            db.create_session(1, {'session_id': 'dev'})
            db.append_messages(1, 'dev', [{'content': 'memory'}])
            self.assertEqual(len(db.get_session_by_id(1, 'dev')[0]['messages']), 1)
            db.close_duckdb_connection()
            self.assertEqual(db.list_sessions(1), [])
        self.assertEqual(len(db.list_sessions(1)), 1)

    def test_ownership_and_delete(self):
        db.append_messages(1, self.sid, [{'content': 'private'}])
        self.assertIsNone(db.get_session_by_id(2, self.sid)[0])
        self.assertEqual(db.append_messages(2, self.sid, [{'content': 'x'}])[1], 'Unauthorized')
        self.assertFalse(db.delete_session(2, self.sid)[0])
        self.assertTrue(db.delete_session(1, self.sid)[0])
        self.assertEqual(db.search_messages(1, 'private'), [])

    def test_new_errors_have_english_and_chinese_translations(self):
        import ast
        from utils.i18n import ZH_CN, translate
        files = ('services/duckdb_service.py', 'routes/ai_api.py')
        for filename in files:
            tree = ast.parse(Path(filename).read_text())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == 'ArchiveError' and node.args
                        and isinstance(node.args[0], ast.Constant)):
                    message = node.args[0].value
                    self.assertIn(message, ZH_CN)
                    with self.app.test_request_context(headers={'Cookie': 'language=zh-CN'}):
                        self.assertEqual(translate(message), ZH_CN[message])
                    with self.app.test_request_context():
                        self.assertEqual(translate(message), message)

    def test_parallel_appends_allocate_unique_sequences(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: db.append_messages(1, self.sid, [{'content': str(i)}]), range(8)))
        self.assertEqual([m['seq'] for m in db.get_session_by_id(1, self.sid)[0]['messages']], list(range(8)))

    def test_multiprocess_access(self):
        import subprocess
        import sys
        result = subprocess.run([sys.executable, '-c',
            'from services.duckdb_service import list_sessions; print(len(list_sessions(1)))'],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '1')

        script = ('from services.duckdb_service import append_messages; '
                  '[append_messages(1, "session", [{"content": str(i)}]) for i in range(4)]')
        children = [subprocess.Popen([sys.executable, '-c', script],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for _ in range(2)]
        for child in children:
            _, stderr = child.communicate(timeout=20)
            self.assertEqual(child.returncode, 0, stderr)
        self.assertEqual([m['seq'] for m in db.get_session_by_id(1, self.sid)[0]['messages']], list(range(8)))
