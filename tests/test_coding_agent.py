import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from services.coding_service import CodingService


class CodingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'projects'
        self.project.mkdir()
        self.agent = CodingService({'PI_DATA_DIR': str(self.root / 'data'),
            'CODING_PROJECT_ROOT': str(self.project), 'PI_WORKSPACE': str(self.root / 'workspace'),
            'PI_SYSTEM_PROMPT_FILE': str(Path('config/pi-system-prompt.txt').resolve())})
        self.user = SimpleNamespace(id=1, username='first')

    def test_path_boundary_and_symlink_escape(self):
        (self.project / 'escape').symlink_to(self.root, target_is_directory=True)
        for path in (self.root, self.project / '..', self.project / 'escape'):
            with self.assertRaises(ValueError):
                self.agent.project(str(path))
        self.assertEqual(self.agent.project(), self.project)

    def test_session_project_native_prompt_and_user_isolation(self):
        metadata = self.agent.create(self.user, {'project': str(self.project)})
        folder = self.agent.folder(self.user, metadata['id'])
        self.assertEqual(self.agent.working_directory(folder), self.project)
        self.assertEqual(metadata['prompt_text'], '')
        self.assertEqual(metadata['settings']['prompt'], 'pi')
        with self.assertRaises(FileNotFoundError):
            self.agent.folder(SimpleNamespace(id=2, username='second'), metadata['id'])

    def test_history_retains_project_association(self):
        second = self.project / 'second'
        second.mkdir()
        self.agent.create(self.user, {'project': str(self.project)})
        self.agent.create(self.user, {'project': str(second)})
        self.assertEqual({s['project'] for s in self.agent.list(self.user)}, {str(self.project), str(second)})

    def test_deleted_project_cannot_run(self):
        child = self.project / 'deleted'
        child.mkdir()
        metadata = self.agent.create(self.user, {'project': str(child)})
        child.rmdir()
        with self.assertRaises(FileNotFoundError):
            self.agent.working_directory(self.agent.folder(self.user, metadata['id']))

    def test_import_native_history_once_preserves_source_and_branches(self):
        agent_dir = self.root / 'agent'
        self.agent.config['CODING_PI_AGENT_DIR'] = str(agent_dir)
        encoded = '--' + str(self.project).lstrip('/').replace('/', '-') + '--'
        sessions = agent_dir / 'sessions' / encoded
        sessions.mkdir(parents=True)
        entries = [
            {'type': 'session', 'version': 3, 'cwd': str(self.project), 'id': 'native'},
            {'type': 'model_change', 'id': 'm', 'parentId': None, 'provider': 'test', 'modelId': 'model'},
            {'type': 'message', 'id': 'u', 'parentId': 'm', 'message': {'role': 'user', 'content': 'Build project'}},
            {'type': 'message', 'id': 'abandoned', 'parentId': 'u', 'message': {'role': 'assistant', 'content': 'Old branch'}},
            {'type': 'message', 'id': 'a', 'parentId': 'u', 'message': {'role': 'assistant', 'content': 'Selected branch'}},
        ]
        source = sessions / 'native.jsonl'
        original = '\n'.join(json.dumps(e) for e in entries) + '\n'
        source.write_text(original)
        self.agent.import_native(self.user, str(self.project))
        metadata = self.agent.list(self.user)[0]
        folder = self.agent.folder(self.user, metadata['id'])
        self.assertEqual([m['content'] for m in self.agent.history(folder)], ['Build project', 'Selected branch'])
        self.assertEqual(metadata['settings']['model'], 'test/model')
        self.assertEqual((folder / 'session.jsonl').read_text(), original)
        self.agent.save(folder, {'role': 'user', 'content': 'Web continuation'})
        self.agent.import_native(self.user, str(self.project))
        self.assertEqual(len(self.agent.list(self.user)), 1)
        self.assertEqual(len(self.agent.history(folder)), 3)
        self.assertEqual(source.read_text(), original)
        entries[0]['cwd'] = str(self.root)
        (sessions / 'wrong.jsonl').write_text('\n'.join(json.dumps(e) for e in entries))
        (sessions / 'broken.jsonl').write_text('{invalid')
        self.agent.import_native(self.user, str(self.project))
        self.assertEqual(len(self.agent.list(self.user)), 1)
