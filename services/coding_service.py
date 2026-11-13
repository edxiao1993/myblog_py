"""Project-backed Pi sessions with private web history."""
import fcntl
import json
import os
import re
import uuid
from pathlib import Path
from services.pi_service import PiService


class CodingService(PiService):
    def __init__(self, config):
        super().__init__({**config, 'PI_DATA_DIR': str(Path(config['PI_DATA_DIR']) / 'coding')})

    def project(self, value=None):
        root = Path(self.config['CODING_PROJECT_ROOT']).expanduser().resolve(strict=True)
        path = Path(value or root).expanduser().resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_dir():
            raise ValueError('Choose a folder within the allowed project root.')
        return path

    def create(self, user, data=None):
        data = data or {}
        project = self.project(data.get('project'))
        metadata = super().create(user, {**data, 'prompt': 'pi'})
        metadata['project'] = str(project)
        self.write_metadata(self.folder(user, metadata['id']), metadata)
        return metadata

    def working_directory(self, folder):
        return self.project(self.metadata(folder)['project'])

    def settings(self, user, data, previous=None, has_messages=False):
        return super().settings(user, {**data, 'prompt': 'pi'}, previous, has_messages)

    def import_native(self, user, project):
        project = self.project(project)
        agent_dir = Path(self.config.get('CODING_PI_AGENT_DIR') or os.environ.get('PI_CODING_AGENT_DIR') or Path.home() / '.pi/agent').expanduser()
        encoded = '--' + re.sub(r'[/\\:]', '-', str(project).lstrip('/\\')) + '--'
        session_dir = agent_dir / 'sessions' / encoded
        if not session_dir.is_dir():
            return
        root = self.user_root(user)
        with (root / '.native-import.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for source in session_dir.glob('*.jsonl'):
                try:
                    if not source.resolve().is_relative_to(session_dir.resolve()):
                        continue
                    sid = uuid.uuid5(uuid.NAMESPACE_URL, str(source.resolve())).hex
                    folder = root / sid
                    if (folder / 'metadata.json').exists():
                        continue
                    raw = source.read_text()
                    entries = [json.loads(line) for line in raw.split('\n') if line.strip()]
                    if not entries or entries[0].get('type') != 'session' or not isinstance(entries[0].get('cwd'), str) or self.project(entries[0]['cwd']) != project:
                        continue
                    nodes = entries[1:]
                    by_id = {e['id']: e for e in nodes if e.get('id')}
                    branch, seen = [], set()
                    node = nodes[-1] if nodes else None
                    while node:
                        key = node.get('id')
                        if key in seen:
                            raise ValueError('Invalid session tree')
                        seen.add(key)
                        branch.append(node)
                        node = by_id.get(node.get('parentId'))
                    branch.reverse()
                    if nodes and not by_id:
                        branch = nodes
                    messages = [e['message'] for e in branch if e.get('type') == 'message']
                    if not messages:
                        continue
                    model, thinking = '', ''
                    for entry in branch:
                        if entry.get('type') == 'model_change':
                            model = entry['provider'] + '/' + entry['modelId']
                        elif entry.get('type') == 'thinking_level_change':
                            thinking = entry.get('thinkingLevel', '')
                    title = 'New conversation'
                    for message in messages:
                        if message.get('role') == 'user':
                            content = message.get('content', '')
                            title = (content if isinstance(content, str) else ' '.join(b.get('text', '') for b in content if b.get('type') == 'text'))[:70] or title
                            break
                    for entry in nodes:
                        if entry.get('type') == 'session_info' and entry.get('name'):
                            title = entry['name']
                    folder.mkdir(exist_ok=True, mode=0o700)
                    (folder / 'session.jsonl').write_text(raw)
                    (folder / 'messages.jsonl').write_text(''.join(json.dumps(m, ensure_ascii=False) + '\n' for m in messages))
                    self.write_metadata(folder, {'id': sid, 'title': title, 'created': source.stat().st_mtime,
                        'username': user.username, 'project': str(project), 'prompt_text': '',
                        'settings': {'model': model, 'thinking': thinking, 'prompt': 'pi'}})
                except (OSError, ValueError, KeyError, TypeError):
                    # Ignore incomplete or incompatible sessions without hiding valid history.
                    continue

    def rename(self, folder, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 200:
            raise ValueError('Enter a session name of 1–200 characters.')
        name = name.strip()
        self.options(folder.parent.name, project=self.working_directory(folder),
                     session_path=folder / 'session.jsonl', session_name=name)
        metadata = self.metadata(folder)
        metadata['title'] = name
        self.write_metadata(folder, metadata)
        return metadata
