"""Optional manual memory update; the web application schedules nightly runs."""
import argparse
import os
from pathlib import Path
import sys

from dotenv import load_dotenv
from services.pi_memory import summarize_users
from services.pi_service import PiService


def main():
    parser = argparse.ArgumentParser(description='Update private long-term memory from Pi chats.')
    parser.add_argument('--user', help='Process only this login name (default: all users with Pi chats).')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    load_dotenv(root / '.env')
    # Resolve defaults from the project, even when cron starts in another directory.
    def path(key, default):
        return str((root / os.environ.get(key, default)).resolve())
    agent = PiService({
        'PI_EXECUTABLE': os.environ.get('PI_EXECUTABLE', 'pi'),
        'PI_DATA_DIR': path('PI_DATA_DIR', 'data/pi'),
        'PI_WORKSPACE': path('PI_WORKSPACE', 'data/pi-workspace'),
        'PI_PROVIDER': os.environ.get('PI_PROVIDER', ''),
        'PI_MODEL': os.environ.get('PI_MODEL', ''),
        'PI_MEMORY_MODEL': os.environ.get('PI_MEMORY_MODEL', ''),
        'PI_TIMEOUT': int(os.environ.get('PI_TIMEOUT', '600')),
    })
    updated, failed = summarize_users(agent, args.user)
    print(f'Memory summaries updated: {updated}; failed: {failed}.')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
