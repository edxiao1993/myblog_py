"""One nightly memory pass per data directory, shared by same-host web workers."""
import atexit
from datetime import datetime
import fcntl
import logging
import os
from pathlib import Path
import tempfile
import threading

from services.pi_memory import summarize_users
from services.pi_service import PiService

logger = logging.getLogger(__name__)
_start_lock = threading.Lock()


class MemoryScheduler:
    def __init__(self, config):
        self.config = dict(config)
        self.hour = int(config.get('PI_MEMORY_HOUR', 3))
        if not 0 <= self.hour <= 23:
            raise ValueError('PI_MEMORY_HOUR must be between 0 and 23')
        self.pid = os.getpid()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name='pi-memory-scheduler', daemon=True)

    def run_due(self, now=None):
        now = now or datetime.now()
        if self.stopped.is_set() or now.hour < self.hour:
            return False
        agent = PiService(self.config)
        today = now.date().isoformat()
        with (agent.root / '.memory-schedule.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            stamp = agent.root / '.memory-last-run'
            if stamp.exists() and stamp.read_text().strip() == today:
                return False
            updated, failed = summarize_users(agent, stop_event=self.stopped)
            if self.stopped.is_set():
                raise InterruptedError('Memory scheduler stopped')
            # Completed passes, including per-user failures, retry on the next day.
            # If the worker dies mid-pass no stamp is written, so another can resume.
            fd, name = tempfile.mkstemp(prefix='.memory-run-', dir=agent.root)
            try:
                with os.fdopen(fd, 'w') as handle:
                    handle.write(today)
                    handle.flush()
                    os.fsync(handle.fileno())
                Path(name).replace(stamp)
            finally:
                Path(name).unlink(missing_ok=True)
            logger.info('Pi memory job completed: updated=%d failed=%d', updated, failed)
            return True

    def _run(self):
        while not self.stopped.is_set():
            try:
                self.run_due()
            except InterruptedError:
                break
            except Exception as error:
                # Never include transcript, memory, paths or provider error bodies.
                logger.error('Pi memory scheduler failed (%s)', type(error).__name__)
            self.stopped.wait(60)

    def stop(self):
        self.stopped.set()
        if self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=5)


def start_memory_scheduler(app):
    if app.testing or not app.config.get('PI_MEMORY_ENABLED', False):
        return None
    with _start_lock:
        scheduler = app.extensions.get('pi_memory_scheduler')
        if scheduler is None or scheduler.pid != os.getpid():
            scheduler = MemoryScheduler(app.config)
            app.extensions['pi_memory_scheduler'] = scheduler
            scheduler.thread.start()
            atexit.register(scheduler.stop)
        return scheduler


def stop_memory_scheduler(app):
    scheduler = app.extensions.get('pi_memory_scheduler')
    if scheduler is not None and scheduler.pid == os.getpid():
        scheduler.stop()
