"""Resolve owned Coding Agent projects and retain legacy Pi conversations."""
import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from services.coding_service import CodingService
from services.pi_service import PiService


class AgentBackend(CodingService):
    def __init__(self, config):
        config = dict(config)
        config.setdefault('CODING_PROJECT_ROOT', str(Path(config['PI_WORKSPACE']) / 'coding-projects-root'))
        super().__init__(config)
        self.legacy = PiService(config)

    def request_root(self, user):
        # Keep old creation idempotency records and the cross-version request lock.
        return self.legacy.user_root(user)

    def folder(self, user, sid):
        try:
            return super().folder(user, sid)
        except FileNotFoundError:
            return self.legacy.folder(user, sid)

    def list(self, user):
        items = {m['id']: m for m in self.legacy.list(user)}
        items.update({m['id']: m for m in super().list(user)})
        return list(items.values())

    def is_coding(self, folder):
        return folder.is_relative_to(self.root)

    def working_directory(self, folder):
        return super().working_directory(folder) if self.is_coding(folder) else self.legacy.working_directory(folder)

    def run(self, folder, *args, **kwargs):
        backend = super().run if self.is_coding(folder) else self.legacy.run
        yield from backend(folder, *args, **kwargs)

    def configure(self, user, folder, data):
        if not self.is_coding(folder):
            return self.legacy.configure(user, folder, data)
        return super().configure(user, folder, data)

    def _assigned(self, user):
        configured = self.config.get('CODING_API_PROJECTS') or {}
        if isinstance(configured, str):
            configured = json.loads(configured)
        if not isinstance(configured, dict):
            raise ValueError('Invalid project configuration.')
        if user.username in configured and not isinstance(configured[user.username], list):
            raise ValueError('Invalid project configuration.')
        return configured.get(user.username)

    def default_project(self, user):
        return Path(self.config['CODING_PROJECT_ROOT']).expanduser().resolve() / 'coding-projects' / self.login_folder(user.username) / 'default'

    @staticmethod
    def project_id(path):
        return 'project-' + hashlib.sha256(str(path).encode()).hexdigest()[:24]

    def directory_id(self, user, metadata):
        if metadata.get('working_directory_id'):
            return metadata['working_directory_id']
        if not metadata.get('project'):
            path = self.legacy.workspace(self.login_folder(user.username))
            return 'legacy-' + hashlib.sha256(str(path).encode()).hexdigest()[:24]
        path = Path(metadata['project']).resolve()
        try:
            assigned = self._assigned(user)
        except (ValueError, TypeError):
            assigned = []
        if assigned is None and path == self.default_project(user):
            return 'default'
        for entry in assigned or []:
            if not isinstance(entry, dict) or not isinstance(entry.get('path'), str) or not isinstance(entry.get('id'), str):
                continue
            if Path(entry['path']).expanduser().resolve() == path:
                return entry['id']
        return self.project_id(path)

    def projects(self, user):
        assigned = self._assigned(user)
        if assigned is None:
            root = Path(self.config['CODING_PROJECT_ROOT']).expanduser()
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            default = self.default_project(user)
            # Resolve before creating anything, including existing symlink parents.
            if not default.resolve().is_relative_to(root.resolve()):
                raise ValueError('Invalid project configuration.')
            prior = super().list(user)
            used = any(m.get('project') == str(default) for m in prior)
            if default.exists() or not used:
                default.mkdir(parents=True, exist_ok=True, mode=0o700)
            assigned = [{'id': 'default', 'name': 'My coding workspace', 'path': str(default)}]
            for metadata in super().list(user):
                if metadata.get('project'):
                    assigned.append({'id': self.directory_id(user, metadata),
                                     'name': Path(metadata['project']).name,
                                     'path': metadata['project']})
        if not isinstance(assigned, list):
            raise ValueError('Invalid project configuration.')
        result = {}
        for entry in assigned:
            if (not isinstance(entry, dict) or not isinstance(entry.get('id'), str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', entry['id']) or entry['id'].startswith('legacy-')
                    or not isinstance(entry.get('name'), str) or not isinstance(entry.get('path'), str)):
                raise ValueError('Invalid project configuration.')
            try:
                path = self.project(entry['path'])
            except (OSError, ValueError):
                continue
            if entry['id'] in result and result[entry['id']]['path'] != str(path):
                raise ValueError('Invalid project configuration.')
            if entry['id'] not in result:
                result[entry['id']] = {'id': entry['id'], 'name': entry['name'], 'path': str(path)}
        return list(result.values())

    def project_context(self, user, directory_id=None):
        choices = self.projects(user)
        if directory_id is None:
            if not choices:
                raise OSError('No approved project is available.')
            return choices[0], choices
        for choice in choices:
            if choice['id'] == directory_id:
                return choice, choices
        for metadata in self.legacy.list(user):
            if self.directory_id(user, metadata) == directory_id:
                choice = {'id': directory_id, 'name': 'Legacy workspace',
                          'path': str(self.legacy.workspace(self.login_folder(user.username)))}
                return choice, choices + [choice]
        raise ValueError('Invalid working_directory_id.')

    def authorize_session(self, user, metadata):
        choice, _ = self.project_context(user, self.directory_id(user, metadata))
        if metadata.get('project') and self.project(metadata['project']) != Path(choice['path']).resolve():
            raise ValueError('This project is no longer approved for the conversation.')

    def create_api_session(self, user, choice, settings):
        # Settings are validated against this project's live Pi options by AgentService.
        path = self.project(choice['path'])
        folder = self.user_root(user) / uuid.uuid4().hex
        folder.mkdir(mode=0o700)
        metadata = {'id': folder.name, 'title': 'New conversation', 'created': time.time(),
                    'username': user.username, 'project': str(path), 'working_directory_id': choice['id'],
                    'settings': {'model': settings['model_id'], 'thinking': settings['thinking_level'] or '', 'prompt': 'pi',
                                 'supports_thinking': settings['thinking_level'] is not None},
                    'prompt_text': ''}
        self.write_metadata(folder, metadata)
        return metadata
