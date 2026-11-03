"""Private local files must stay outside the shared repository."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class GitIgnoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_private_files_and_virtualenv_symlink_are_ignored(self):
        shutil.copy2(Path(__file__).resolve().parents[1] / '.gitignore', self.root / '.gitignore')
        subprocess.run(['git', 'init', '--quiet'], cwd=self.root, check=True)
        target = self.root / 'shared-venv'
        target.mkdir()
        (self.root / '.venv').symlink_to(target, target_is_directory=True)
        for name in ('deploy.sh', '.venv', '.env', '.deploy.env', 'AGENTS.local.md',
                     '.local/legacy-myblog/history.bundle', 'certBlueNest/server.key', 'note.md'):
            result = subprocess.run(['git', 'check-ignore', '-q', name], cwd=self.root)
            self.assertEqual(result.returncode, 0, name)
        result = subprocess.run(['git', 'check-ignore', '-q', '.deploy.env.example'], cwd=self.root)
        self.assertEqual(result.returncode, 1)
