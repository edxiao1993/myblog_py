"""Deployment configuration checks; never contact a real server."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project with spaces'
        self.root.mkdir()
        shutil.copy2(Path(__file__).resolve().parents[1] / 'deploy.sh', self.root / 'deploy.sh')
        self.args = self.root / 'rsync-args'
        self.ssh_args = self.root / 'ssh-args'
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        stub = bin_dir / 'rsync'
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$DEPLOY_TEST_ARGS"\n')
        stub.chmod(0o700)
        stub = bin_dir / 'ssh'
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$DEPLOY_TEST_SSH_ARGS"\n')
        stub.chmod(0o700)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('DEPLOY_')}
        self.env.update(PATH=str(bin_dir) + os.pathsep + os.environ['PATH'],
                        DEPLOY_TEST_ARGS=str(self.args), DEPLOY_TEST_SSH_ARGS=str(self.ssh_args))

    def run_deploy(self, **overrides):
        return subprocess.run(['bash', str(self.root / 'deploy.sh')],
                              cwd=self.temp.name, env={**self.env, **overrides},
                              input='y\n', capture_output=True, text=True, timeout=10)

    def configure(self):
        (self.root / '.deploy.env').write_text(
            'DEPLOY_SERVER="${DEPLOY_SERVER:-appuser@example.test}"\n'
            'DEPLOY_DIR="${DEPLOY_DIR:-/srv/existing-app}"\n'
            'DEPLOY_SERVICE="${DEPLOY_SERVICE:-existing-app.service}"\n')

    def test_local_config_loads_from_project_and_preserves_private_files(self):
        self.configure()
        result = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.args.read_text().splitlines()
        self.assertEqual(args[-1], 'appuser@example.test:/srv/existing-app/')
        for name in ('.env', '.deploy.env*', '.local/', '.venv', 'note.md', '*.key', 'data/', 'logs/'):
            self.assertIn(name, args)
        self.assertIn('existing-app.service', self.ssh_args.read_text())

    def test_explicit_environment_overrides_local_defaults(self):
        self.configure()
        result = self.run_deploy(DEPLOY_SERVER='other@example.test', DEPLOY_DIR='/srv/other',
                                 DEPLOY_SERVICE='other.service')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.args.read_text().splitlines()[-1], 'other@example.test:/srv/other/')
        self.assertIn('other.service', self.ssh_args.read_text())

    def test_missing_destination_and_invalid_service_do_not_deploy(self):
        self.assertNotEqual(self.run_deploy().returncode, 0)
        self.assertFalse(self.args.exists())
        self.configure()
        self.assertNotEqual(self.run_deploy(DEPLOY_SERVICE='unsafe; command').returncode, 0)
        self.assertFalse(self.args.exists())

    def test_private_files_and_virtualenv_symlink_are_ignored(self):
        shutil.copy2(Path(__file__).resolve().parents[1] / '.gitignore', self.root / '.gitignore')
        subprocess.run(['git', 'init', '--quiet'], cwd=self.root, check=True)
        target = self.root / 'shared-venv'
        target.mkdir()
        (self.root / '.venv').symlink_to(target, target_is_directory=True)
        for name in ('.venv', '.env', '.deploy.env', 'AGENTS.local.md',
                     '.local/legacy-myblog/history.bundle', 'certBlueNest/server.key', 'note.md'):
            result = subprocess.run(['git', 'check-ignore', '-q', name], cwd=self.root)
            self.assertEqual(result.returncode, 0, name)
        result = subprocess.run(['git', 'check-ignore', '-q', '.deploy.env.example'], cwd=self.root)
        self.assertEqual(result.returncode, 1)
