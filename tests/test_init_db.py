"""Initialization regressions; never connect to the project's configured DB."""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from werkzeug.security import check_password_hash


ROOT = Path(__file__).resolve().parents[1]


class InitDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'test.db'

    def run_init(self, password=''):
        env = dict(os.environ, DATABASE_URL=f'sqlite:///{self.path}',
                   DEFAULT_ADMIN_PASSWORD=password, DEFAULT_ADMIN_USERNAME='admin',
                   DEFAULT_ADMIN_EMAIL='admin@example.com')
        return subprocess.run(
            [sys.executable, str(ROOT / 'init_db.py')],
            cwd=self.directory.name, env=env, capture_output=True, text=True,
            timeout=30,
        )

    def test_all_tables_without_admin_password(self):
        result = self.run_init()
        self.assertEqual(result.returncode, 0, result.stderr)
        with sqlite3.connect(self.path) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            self.assertEqual(tables, {'user', 'post', 'secret_moment', 'refresh_token'})
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM user').fetchone()[0], 0)

    def test_rerun_preserves_user_password_and_moments(self):
        result = self.run_init('first-test-password')
        self.assertEqual(result.returncode, 0, result.stderr)
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute('SELECT username, email FROM user').fetchone(),
                             ('admin', 'admin@example.com'))
            password_hash = conn.execute('SELECT password_hash FROM user').fetchone()[0]
            self.assertTrue(check_password_hash(password_hash, 'first-test-password'))
            conn.execute("INSERT INTO secret_moment (id, moment) VALUES (?, ?)",
                         ('test-moment', 'Hello 手机 📱'))
        result = self.run_init('replacement-test-password')
        self.assertEqual(result.returncode, 0, result.stderr)
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute('SELECT password_hash FROM user').fetchall(),
                             [(password_hash,)])
            self.assertEqual(conn.execute('SELECT moment FROM secret_moment').fetchall(),
                             [('Hello 手机 📱',)])

    def test_missing_database_url_fails_cleanly(self):
        env = dict(os.environ, DATABASE_URL='')
        result = subprocess.run(
            [sys.executable, str(ROOT / 'init_db.py')], env=env,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn('Set DATABASE_URL', result.stderr)
        self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
