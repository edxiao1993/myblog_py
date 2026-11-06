"""Private, incremental Pi memory; no database or provider calls."""
import fcntl
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from services.pi_memory import PiMemory, BATCH_CHARS
from services.pi_service import PiService


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.user = self.root / 'users' / 'alice'
        self.chat = self.user / ('a' * 32)
        self.chat.mkdir(parents=True)
        self.agent = SimpleNamespace(config={'PI_TIMEOUT': 5}, root=self.root)
        self.memory = PiMemory(self.agent)
        self.memory.summarize = Mock(return_value='Alice prefers short answers.')

    def append(self, role, content):
        with (self.chat / 'messages.jsonl').open('a') as handle:
            handle.write(json.dumps({'role': role, 'content': content}) + '\n')

    def test_incremental_summary_and_user_isolation(self):
        self.append('user', 'Please keep answers short.')
        self.append('assistant', [{'type': 'thinking', 'thinking': 'private reasoning'},
                                  {'type': 'text', 'text': 'Understood.'}])
        self.append('toolResult', 'tool output')
        self.assertTrue(self.memory.update(self.user))
        self.assertEqual(self.memory.read(self.user), 'Alice prefers short answers.')
        first_input = self.memory.summarize.call_args.args[1]
        self.assertIn('Please keep answers short.', first_input)
        self.assertIn('Understood.', first_input)
        self.assertNotIn('private reasoning', first_input)
        self.assertNotIn('tool output', first_input)
        self.assertEqual(self.memory.read(self.root / 'users' / 'bob'), '')
        self.assertFalse(self.memory.update(self.user))
        self.assertEqual(self.memory.summarize.call_count, 1)
        self.append('user', 'I am learning French.')
        self.memory.update(self.user)
        previous, new = self.memory.summarize.call_args.args
        self.assertEqual(previous, 'Alice prefers short answers.')
        self.assertIn('learning French', new)
        self.assertNotIn('Please keep answers short.', new)

    def test_large_escaped_and_unicode_messages_are_batched_without_loss(self):
        text = '\x00' * 5000 + '你好' * 20000
        self.append('user', text)
        self.memory.update(self.user)
        batches = [call.args[1] for call in self.memory.summarize.call_args_list]
        self.assertGreater(len(batches), 1)
        self.assertTrue(all(len(batch) <= BATCH_CHARS for batch in batches))
        restored = ''.join(json.loads(line)['text'] for batch in batches for line in batch.splitlines())
        self.assertEqual(restored, text)
        self.assertEqual((self.user / 'memory.json').stat().st_mode & 0o777, 0o600)

    def test_failed_batch_preserves_summary_and_checkpoint(self):
        self.append('user', 'old fact')
        self.memory.update(self.user)
        before = (self.user / 'memory.json').read_bytes()
        self.append('user', 'x' * 60000)
        self.memory.summarize.side_effect = ['intermediate', RuntimeError('provider unavailable')]
        with self.assertRaises(RuntimeError):
            self.memory.update(self.user)
        self.assertEqual((self.user / 'memory.json').read_bytes(), before)
        self.memory.summarize.side_effect = None
        self.memory.summarize.return_value = 'updated'
        self.assertTrue(self.memory.update(self.user))
        self.assertEqual(self.memory.read(self.user), 'updated')

    def test_running_conversation_and_overlapping_job_are_skipped(self):
        self.append('user', 'hello')
        with (self.chat / 'run.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertFalse(self.memory.update(self.user))
        with (self.user / '.memory.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertFalse(self.memory.update(self.user))
        self.assertTrue(self.memory.update(self.user))

    def test_partial_line_waits_for_completion_and_replaced_file_restarts(self):
        path = self.chat / 'messages.jsonl'
        path.write_text('{"role":"user","content":"unfinished"}')
        self.assertFalse(self.memory.update(self.user))
        with path.open('a') as handle:
            handle.write('\n')
        self.assertTrue(self.memory.update(self.user))
        other = self.chat / 'replacement'
        other.write_text(json.dumps({'role': 'user', 'content': 'replacement message'}) + '\n')
        other.replace(path)
        self.assertTrue(self.memory.update(self.user))
        self.assertIn('replacement message', self.memory.summarize.call_args.args[1])

    def test_corrupt_memory_does_not_break_chat_read_or_get_overwritten(self):
        (self.user / 'memory.json').write_text('broken')
        self.assertEqual(self.memory.read(self.user), '')
        self.append('user', 'hello')
        with self.assertRaises(ValueError):
            self.memory.update(self.user)
        self.assertEqual((self.user / 'memory.json').read_text(), 'broken')


class PiSummaryProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fake = self.root / 'pi'
        fake.write_text('#!' + sys.executable + '\n' + '''import json, os, sys, time
from pathlib import Path
request = json.loads(sys.stdin.read())
Path(__file__).with_suffix('.request').write_text(json.dumps({'args': sys.argv, 'input': request, 'pid': os.getpid()}))
mode = request['new_records']
if mode == 'timeout': time.sleep(30)
if mode == 'failure': sys.exit(1)
message = {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Private summary.'}]}
if mode == 'empty': message['content'] = []
if mode == 'oversized': message['content'][0]['text'] = 'x' * 8001
if mode == 'provider-error': message['errorMessage'] = 'private error body'
print(json.dumps({'type': 'message_end', 'message': message}))
''')
        fake.chmod(0o700)
        self.agent = PiService({'PI_EXECUTABLE': str(fake), 'PI_DATA_DIR': str(self.root / 'data'),
            'PI_WORKSPACE': str(self.root / 'workspace'), 'PI_MEMORY_MODEL': 'test/summary-model', 'PI_TIMEOUT': 5})
        self.memory = PiMemory(self.agent)

    def test_print_mode_uses_stdin_no_tools_no_session_and_optional_model(self):
        self.assertEqual(self.memory.summarize('old memory', 'new facts'), 'Private summary.')
        record = json.loads((self.root / 'pi.request').read_text())
        self.assertEqual(record['input']['previous_summary'], 'old memory')
        self.assertNotIn('new facts', record['args'])
        for flag in ['--print', '--no-session', '--no-tools', '--no-extensions', '--no-context-files']:
            self.assertIn(flag, record['args'])
        self.assertEqual(record['args'][record['args'].index('--model') + 1], 'summary-model')
        for mode in ['failure', 'empty', 'oversized', 'provider-error']:
            with self.subTest(mode=mode), self.assertRaises((ValueError, RuntimeError)):
                self.memory.summarize('', mode)

    def test_timeout_terminates_the_process(self):
        self.agent.config['PI_TIMEOUT'] = .3
        with self.assertRaises(subprocess.TimeoutExpired):
            self.memory.summarize('', 'timeout')
        pid = json.loads((self.root / 'pi.request').read_text())['pid']
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_scheduler_shutdown_cancels_and_cleans_up_active_process(self):
        stop = threading.Event()
        self.memory.stop_event = stop
        failures = []
        def run():
            try:
                self.memory.summarize('', 'timeout')
            except Exception as error:
                failures.append(error)
        thread = threading.Thread(target=run)
        thread.start()
        try:
            deadline = time.monotonic() + 3
            request = self.root / 'pi.request'
            while not request.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(request.exists())
        finally:
            stop.set()
            thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(failures[0], InterruptedError)
        pid = json.loads(request.read_text())['pid']
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


class MemoryCommandTests(unittest.TestCase):
    def test_command_continues_after_failure_and_never_logs_raw_error(self):
        from summarize_pi_memory import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for login in ['alice', 'bob']:
                (root / 'users' / login).mkdir(parents=True)
            env = {'PI_DATA_DIR': str(root), 'PI_WORKSPACE': str(root / 'workspace')}
            with patch.dict(os.environ, env, clear=True), patch('sys.argv', ['summarize_pi_memory.py']), \
                    patch('summarize_pi_memory.load_dotenv'), \
                    patch('services.pi_memory.PiMemory.update', side_effect=[RuntimeError('PRIVATE CONVERSATION'), True]) as update, \
                    patch('sys.stdout', new_callable=io.StringIO) as out, self.assertLogs('services.pi_memory') as logs:
                self.assertEqual(main(), 1)
                self.assertEqual(update.call_count, 2)
                self.assertIn('updated: 1; failed: 1', out.getvalue())
                self.assertNotIn('PRIVATE CONVERSATION', str(logs.output))
                self.assertIn('RuntimeError', str(logs.output))

    def test_user_filter_encodes_login_names(self):
        from summarize_pi_memory import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for login in ['alice%2Fbob', 'other']:
                (root / 'users' / login).mkdir(parents=True)
            with patch.dict(os.environ, {'PI_DATA_DIR': str(root)}, clear=True), \
                    patch('sys.argv', ['summarize_pi_memory.py', '--user', 'alice/bob']), \
                    patch('summarize_pi_memory.load_dotenv'), \
                    patch('services.pi_memory.PiMemory.update', return_value=True) as update, \
                    patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(main(), 0)
                self.assertEqual(update.call_args.args[0], root.resolve() / 'users' / 'alice%2Fbob')


if __name__ == '__main__':
    unittest.main()
