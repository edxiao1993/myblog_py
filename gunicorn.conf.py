"""Automatically loaded from the application's working directory by Gunicorn."""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
LOG_DIRECTORY = Path(os.environ.get('LOG_DIR', str(ROOT / 'logs')))

# Do not record URLs, query strings, client addresses, cookies or headers.
access_log_format = 'pid=%(p)s method=%(m)s status=%(s)s duration_s=%(L)s request_id=%({x-request-id}o)s'
loglevel = 'info'
logconfig_dict = {
    'version': 1,
    'disable_existing_loggers': False,
    # Replace Gunicorn's default root handler list when replacing its handlers.
    'root': {'handlers': [], 'level': 'INFO'},
    'formatters': {
        'diagnostic': {'class': 'utils.logger.DiagnosticFormatter', 'format': '[%(asctime)s] %(levelname)s pid=%(process)d %(name)s: %(message)s',
                       'datefmt': '%Y-%m-%d %H:%M:%S'},
    },
    'handlers': {
        name: {'class': 'utils.logger.DatedSizeFileHandler',
               'filename': str(LOG_DIRECTORY / filename), 'formatter': 'diagnostic'}
        for name, filename in [('access', 'gunicorn_access.log'), ('error', 'gunicorn_error.log')]
    },
    'loggers': {
        'gunicorn.access': {'handlers': ['access'], 'level': 'INFO', 'propagate': False},
        'gunicorn.error': {'handlers': ['error'], 'level': 'INFO', 'propagate': False},
    },
}


def on_reload(server):
    # HUP reloads workers, but Gunicorn otherwise keeps the old logger config.
    server.log.cfg = server.cfg
    server.log.setup(server.cfg)


def post_worker_init(worker):
    # Starts even without requests, including when Gunicorn uses --preload.
    app = worker.wsgi
    if hasattr(app, 'extensions'):
        from services.pi_memory_scheduler import start_memory_scheduler
        start_memory_scheduler(app)


def worker_exit(server, worker):
    app = getattr(worker, 'wsgi', None)
    if hasattr(app, 'extensions'):
        from services.pi_memory_scheduler import stop_memory_scheduler
        stop_memory_scheduler(app)
