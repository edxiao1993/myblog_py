"""In-app scheduling uses local time and a shared lock across web workers."""
from datetime import datetime
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from flask import Flask
from services.pi_memory_scheduler import MemoryScheduler, start_memory_scheduler, stop_memory_scheduler


def scheduled_worker(config, ready):
    from services.pi_memory_scheduler import MemoryScheduler
    def job(agent, stop_event=None):
        with (agent.root / 'calls').open('a') as handle:
            handle.write('called\n')
        time.sleep(.15)
        return 1, 0
    with patch('services.pi_memory_scheduler.summarize_users', side_effect=job):
        ready.wait(5)
        MemoryScheduler(config).run_due(datetime(2026, 11, 4, 3, 0))


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = {'PI_MEMORY_ENABLED': True, 'PI_MEMORY_HOUR': 3,
                       'PI_DATA_DIR': str(self.root), 'PI_WORKSPACE': str(self.root / 'workspace')}

    def test_runs_once_per_day_and_catches_up_after_restart(self):
        with patch('services.pi_memory_scheduler.summarize_users', return_value=(1, 0)) as job:
            scheduler = MemoryScheduler(self.config)
            self.assertFalse(scheduler.run_due(datetime(2026, 11, 4, 2, 59)))
            self.assertTrue(scheduler.run_due(datetime(2026, 11, 4, 3, 0)))
            self.assertFalse(MemoryScheduler(self.config).run_due(datetime(2026, 11, 4, 21, 0)))
            self.assertTrue(MemoryScheduler(self.config).run_due(datetime(2026, 11, 5, 18, 0)))
            self.assertEqual(job.call_count, 2)

    def test_failed_users_retry_next_day_without_repeated_daily_charges(self):
        with patch('services.pi_memory_scheduler.summarize_users', return_value=(0, 1)) as job:
            scheduler = MemoryScheduler(self.config)
            self.assertTrue(scheduler.run_due(datetime(2026, 11, 4, 4, 0)))
            self.assertFalse(scheduler.run_due(datetime(2026, 11, 4, 5, 0)))
            self.assertTrue(scheduler.run_due(datetime(2026, 11, 5, 4, 0)))
            self.assertEqual(job.call_count, 2)

    def test_overlapping_workers_only_execute_one_job(self):
        context = multiprocessing.get_context('spawn')
        ready = context.Event()
        workers = [context.Process(target=scheduled_worker, args=(self.config, ready)) for _ in range(2)]
        for worker in workers:
            worker.start()
        ready.set()
        for worker in workers:
            worker.join(10)
            self.assertFalse(worker.is_alive())
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual((self.root / 'calls').read_text(), 'called\n')

    def test_cancelled_job_does_not_mark_the_day_complete(self):
        scheduler = MemoryScheduler(self.config)
        with patch('services.pi_memory_scheduler.summarize_users', side_effect=InterruptedError):
            with self.assertRaises(InterruptedError):
                scheduler.run_due(datetime(2026, 11, 4, 4, 0))
        with patch('services.pi_memory_scheduler.summarize_users', return_value=(1, 0)) as job:
            self.assertTrue(scheduler.run_due(datetime(2026, 11, 4, 4, 1)))
            job.assert_called_once()

    def test_start_is_idempotent_and_testing_and_disabled_apps_do_not_start(self):
        app = Flask(__name__)
        app.config.update(self.config)
        app.testing = True
        self.assertIsNone(start_memory_scheduler(app))
        app.testing = False
        app.config['PI_MEMORY_ENABLED'] = False
        self.assertIsNone(start_memory_scheduler(app))
        app.config['PI_MEMORY_ENABLED'] = True
        entered = threading.Event()
        with patch.object(MemoryScheduler, 'run_due', side_effect=lambda: entered.set()):
            scheduler = start_memory_scheduler(app)
            self.addCleanup(stop_memory_scheduler, app)
            self.assertTrue(entered.wait(2))
            self.assertIs(start_memory_scheduler(app), scheduler)
            stop_memory_scheduler(app)
            self.assertFalse(scheduler.thread.is_alive())

    def test_gunicorn_preload_starts_job_without_requests(self):
        project = Path(__file__).resolve().parents[1]
        chat = self.root / 'users' / 'alice' / ('a' * 32)
        chat.mkdir(parents=True)
        (chat / 'messages.jsonl').write_text(json.dumps({'role': 'user', 'content': 'Remember concise answers.'}) + '\n')
        fake = self.root / 'fake-pi'
        fake.write_text('#!' + sys.executable + '\n' + '''import json, sys
from pathlib import Path
json.loads(sys.stdin.read())
with Path(__file__).with_suffix('.calls').open('a') as handle:
    handle.write('called\\n')
print(json.dumps({'type': 'message_end', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Prefers concise answers.'}]}}))
''')
        fake.chmod(0o700)
        config = {**self.config, 'PI_MEMORY_HOUR': 0, 'PI_EXECUTABLE': str(fake), 'PI_TIMEOUT': 5}
        module = self.root / 'memory_probe.py'
        module.write_text('from flask import Flask\napp = Flask(__name__)\napp.config.update(' + repr(config) + ')\n')
        process = subprocess.Popen([sys.executable, '-m', 'gunicorn', '--config', str(project / 'gunicorn.conf.py'),
            '--bind', '127.0.0.1:0', '--workers', '2', '--preload', 'memory_probe:app'], cwd=project,
            env={**os.environ, 'LOG_DIR': str(self.root / 'logs'),
                 'PYTHONPATH': str(self.root) + os.pathsep + str(project)},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 8
            while not (self.root / '.memory-last-run').exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                time.sleep(.02)
            self.assertIsNone(process.poll())
            self.assertTrue((self.root / '.memory-last-run').exists())
            state = json.loads((chat.parent / 'memory.json').read_text())
            self.assertEqual(state['summary'], 'Prefers concise answers.')
            self.assertEqual(fake.with_suffix('.calls').read_text(), 'called\n')
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def test_invalid_schedule_is_rejected(self):
        self.config['PI_MEMORY_HOUR'] = 24
        with self.assertRaises(ValueError):
            MemoryScheduler(self.config)


if __name__ == '__main__':
    unittest.main()
