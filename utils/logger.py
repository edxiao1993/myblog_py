"""Application file logging with process-safe size/date rotation and retention."""
from contextlib import contextmanager
import copy
from datetime import datetime
import fcntl
import logging
import os
from pathlib import Path
import re
import threading
import time

LOG_DIR = Path(__file__).resolve().parents[1] / 'logs'
MAX_LOG_BYTES = 20 * 1024 * 1024  # 20 MiB: readable files without excessive rotation.
RETENTION_DAYS = 30
_initialized = False


class DiagnosticFormatter(logging.Formatter):
    def format(self, record):
        if record.exc_info:
            record = copy.copy(record)
            record.msg, record.args, record.exc_text = 'Unhandled exception', (), None
        return super().format(record)

    def formatException(self, exc_info):
        import json
        from utils.agent_logging import describe_error
        return json.dumps(describe_error(exc_info[1]), separators=(',', ':'))


class DatedSizeFileHandler(logging.Handler):
    """Open on each write so every Gunicorn worker sees rotations immediately."""
    def __init__(self, filename, max_bytes=MAX_LOG_BYTES, retention_days=RETENTION_DAYS):
        super().__init__()
        self.path = Path(filename)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.history = self.path.parent / 'history'
        self.history.mkdir(exist_ok=True)
        self.max_bytes = max_bytes
        self.retention_days = retention_days
        self.archive_pattern = re.compile(
            rf'^{re.escape(self.path.stem)}(?:\.\d{{4}}-\d{{2}}-\d{{2}}\.\d+\.log|\.log\.\d+)$')
        self.cleanup()
        with self.file_lock():
            descriptor = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            os.close(descriptor)

    @contextmanager
    def file_lock(self):
        descriptor = os.open(str(self.path) + '.lock', os.O_CREAT | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _cleanup(self):
        cutoff = time.time() - self.retention_days * 86400
        for directory in (self.path.parent, self.history):
            for file in directory.iterdir():
                if not self.archive_pattern.fullmatch(file.name) or not file.is_file() or file.is_symlink():
                    continue
                if file.stat().st_mtime < cutoff:
                    file.unlink(missing_ok=True)
                elif directory == self.path.parent:
                    destination = self.history / file.name
                    if destination.exists():
                        day = datetime.fromtimestamp(file.stat().st_mtime).strftime('%Y-%m-%d')
                        destination = self._archive_path(day)
                    file.rename(destination)

    def _archive_path(self, day):
        index = 1
        archive = self.history / f'{self.path.stem}.{day}.{index:03d}.log'
        while archive.exists():
            index += 1
            archive = self.history / f'{self.path.stem}.{day}.{index:03d}.log'
        return archive

    def cleanup(self):
        with self.file_lock():
            # An idle active file must also expire even without another request.
            if self.path.exists() and self.path.stat().st_mtime < time.time() - self.retention_days * 86400:
                self.path.unlink()
            self._cleanup()

    def emit(self, record):
        try:
            data = (self.format(record) + '\n').encode('utf-8', errors='replace')
            limit = min(self.max_bytes, 64 * 1024)
            if len(data) > limit:
                data = data[:max(0, limit - 14)].decode('utf-8', errors='ignore').encode() + b' [truncated]\n'
            with self.file_lock():
                if self.path.exists() and self.path.stat().st_size:
                    stat = self.path.stat()
                    day = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d')
                    if stat.st_size + len(data) > self.max_bytes or day != datetime.now().strftime('%Y-%m-%d'):
                        archive = self._archive_path(day)
                        self.path.rename(archive)
                descriptor = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, 'ab') as handle:
                    handle.write(data)
        except Exception:
            self.handleError(record)


def setup_logging(app=None):
    global _initialized
    if _initialized:
        return
    directory = Path(os.environ.get('LOG_DIR', str(LOG_DIR)))
    formatter = DiagnosticFormatter('[%(asctime)s] %(levelname)s pid=%(process)d %(name)s: %(message)s',
                                  datefmt='%Y-%m-%d %H:%M:%S')
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)
    general = DatedSizeFileHandler(directory / 'myblog.log')
    general.setFormatter(formatter)
    root.addHandler(general)

    agent = logging.getLogger('pi.agent')
    agent.setLevel(logging.INFO)
    agent.propagate = False
    normal = DatedSizeFileHandler(directory / 'agent.log')
    normal.addFilter(lambda record: record.levelno < logging.ERROR)
    errors = DatedSizeFileHandler(directory / 'agent_error.log')
    errors.setLevel(logging.ERROR)
    for handler in (normal, errors):
        handler.setFormatter(formatter)
        agent.addHandler(handler)
    # Gunicorn owns these writers; the application also expires idle archives.
    gunicorn_logs = [DatedSizeFileHandler(directory / name)
                     for name in ('gunicorn_access.log', 'gunicorn_error.log')]
    # Do not enable SQL parameter/debug logging: it can contain private data.
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
    _initialized = True

    def retention():
        while True:
            for handler in (general, normal, errors, *gunicorn_logs):
                try:
                    handler.cleanup()
                except OSError:
                    root.error('log.cleanup_failed file=%s', handler.path.name)
            time.sleep(3600)

    threading.Thread(target=retention, name='log-retention', daemon=True).start()
    root.info('Logging initialized: 20 MiB files, daily rotation, 30-day retention')
