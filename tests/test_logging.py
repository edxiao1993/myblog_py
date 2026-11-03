import logging
import multiprocessing
import subprocess
import sys
import os
from pathlib import Path
import tempfile
import time
import unittest


def write_worker(directory, worker):
    from utils.logger import DatedSizeFileHandler
    logger = logging.Logger('worker', logging.INFO)
    handler = DatedSizeFileHandler(Path(directory) / 'agent.log', max_bytes=160)
    logger.addHandler(handler)
    for index in range(20):
        logger.info('worker-%d-record-%d', worker, index)
    handler.close()


class FileLoggingTests(unittest.TestCase):
    def setUp(self):
        from utils.logger import DatedSizeFileHandler
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.handler = DatedSizeFileHandler(self.root / 'agent.log', max_bytes=160)
        self.addCleanup(self.handler.close)
        self.logger = logging.Logger('rotation-test', logging.INFO)
        self.logger.addHandler(self.handler)

    def test_rotates_at_size_and_keeps_every_record(self):
        expected = [f'status-{index:03d}-' + 'x' * 25 for index in range(30)]
        for message in expected:
            self.logger.info(message)
        files = list(self.root.glob('*.log'))
        self.assertGreater(len(files), 1)
        records = []
        for file in files:
            self.assertLessEqual(file.stat().st_size, 160)
            if file.name != 'agent.log':
                self.assertRegex(file.name, r'^agent\.\d{4}-\d{2}-\d{2}\.\d+\.log$')
            records.extend(file.read_text().splitlines())
        self.assertCountEqual(records, expected)

    def test_retention_removes_only_old_owned_archives(self):
        old = self.root / 'agent.2020-01-01.001.log'
        recent = self.root / 'agent.2099-01-01.001.log'
        unrelated = self.root / 'private-notes.txt'
        for path in [old, recent, unrelated]:
            path.write_text('preserve or expire')
        timestamp = time.time() - 31 * 86400
        os.utime(old, (timestamp, timestamp))
        os.utime(unrelated, (timestamp, timestamp))
        self.handler.cleanup()
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(unrelated.exists())

    def test_daily_rotation(self):
        self.logger.info('yesterday')
        timestamp = time.time() - 86400
        os.utime(self.root / 'agent.log', (timestamp, timestamp))
        self.logger.info('today')
        self.assertEqual((self.root / 'agent.log').read_text().strip(), 'today')
        self.assertEqual(len(list(self.root.glob('agent.*.log'))), 1)

    def test_error_classification_never_contains_raw_message(self):
        from utils.agent_logging import describe_error
        result = describe_error(RuntimeError('HTTP 502 upstream body includes secret-token-value and private chat'))
        self.assertEqual(result['http_status'], 502)
        self.assertNotIn('secret-token-value', str(result))
        self.assertNotIn('private chat', str(result))

    def test_multiple_processes_rotate_without_losing_records(self):
        workers = [multiprocessing.Process(target=write_worker, args=(str(self.root), index)) for index in range(3)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
            self.assertEqual(worker.exitcode, 0)
        records = []
        for file in self.root.glob('*.log'):
            records.extend(file.read_text().splitlines())
            self.assertLessEqual(file.stat().st_size, 160)
        self.assertCountEqual(records, [f'worker-{worker}-record-{index}' for worker in range(3) for index in range(20)])

    def test_real_setup_routes_errors_to_separate_files(self):
        script = """
import logging, logging.config, runpy
from utils.logger import setup_logging
from utils.agent_logging import agent_event, describe_error
config = runpy.run_path('gunicorn.conf.py')
logging.config.dictConfig(config['logconfig_dict'])
setup_logging()
agent_event('request.ok', request_id='abc')
agent_event('request.failed', logging.ERROR, **describe_error(RuntimeError('HTTP 502 secret-never-log')))
logging.getLogger('gunicorn.error').error('worker.failed')
logging.getLogger().info('application.started')
try:
    raise RuntimeError('secret-exception-body')
except RuntimeError:
    logging.getLogger().exception('secret-exception-message')
"""
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, '-c', script], cwd=root,
                                env={**os.environ, 'LOG_DIR': str(self.root)}, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        normal = (self.root / 'agent.log').read_text()
        errors = (self.root / 'agent_error.log').read_text()
        self.assertIn('request.ok', normal)
        self.assertNotIn('request.failed', normal)
        self.assertIn('request.failed', errors)
        self.assertIn('502', errors)
        self.assertNotIn('request.ok', errors)
        self.assertNotIn('secret-never-log', normal + errors)
        self.assertIn('worker.failed', (self.root / 'gunicorn_error.log').read_text())
        general = (self.root / 'myblog.log').read_text()
        self.assertIn('application.started', general)
        self.assertIn('RuntimeError', general)
        self.assertNotIn('secret-exception', general)

    def test_gunicorn_access_metadata_and_hup_reload(self):
        import signal
        import socket
        from urllib.request import build_opener, ProxyHandler
        root = Path(__file__).resolve().parents[1]
        module = self.root / 'logging_probe.py'
        module.write_text("def app(environ, start_response):\n    start_response('200 OK', [('Content-Type', 'text/plain'), ('X-Request-ID', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')])\n    return [b'ok']\n")
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        directory = self.root / 'gunicorn-logs'
        process = subprocess.Popen([sys.executable, '-m', 'gunicorn', '--config', str(root / 'gunicorn.conf.py'),
                                    '--bind', f'127.0.0.1:{port}', '--workers', '1', '--threads', '2', 'logging_probe:app'],
                                   cwd=root, env={**os.environ, 'LOG_DIR': str(directory),
                                                 'PYTHONPATH': str(self.root) + os.pathsep + str(root)},
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        opener = build_opener(ProxyHandler({}))
        def ready():
            last_error = ''
            for _ in range(100):
                try:
                    with opener.open(f'http://127.0.0.1:{port}/private-path?token=private-query', timeout=.5) as response:
                        self.assertEqual(response.read(), b'ok')
                        return
                except OSError as error:
                    last_error = type(error).__name__
                    if process.poll() is not None:
                        break
                    time.sleep(.05)
            diagnostic = directory / 'gunicorn_error.log'
            self.fail(f'Gunicorn did not start/reload: exit={process.poll()}, {last_error}; ' + (diagnostic.read_text() if diagnostic.exists() else 'no error log'))
        try:
            ready()
            process.send_signal(signal.SIGHUP)
            for _ in range(100):
                if (directory / 'gunicorn_error.log').read_text().count('Booting worker') >= 2:
                    break
                time.sleep(.05)
            ready()
            access = (directory / 'gunicorn_access.log').read_text()
            self.assertIn('request_id=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', access)
            self.assertNotIn('private-path', access)
            self.assertNotIn('private-query', access)
            self.assertGreaterEqual((directory / 'gunicorn_error.log').read_text().count('Booting worker'), 2)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
