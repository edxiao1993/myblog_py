import atexit
import fcntl
import hashlib
import os
import re
import signal
import tempfile
from functools import wraps
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from services.pi_service import BusyError, PiService
from services.agent_backend import AgentBackend

logger = logging.getLogger(__name__)

# Global tracking of active runs across sessions: session_id -> {run_id, meta, folder}
_ACTIVE_RUNS = {}
_SHUTTING_DOWN = False
_SERVICE_LOCK = threading.Lock()


def _atomic_write(path, text):
    fd, name = tempfile.mkstemp(prefix='.api-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        Path(name).replace(path)
    finally:
        Path(name).unlink(missing_ok=True)


def _serialize_requests(method):
    @wraps(method)
    def wrapped(self, user, *args, **kwargs):
        path = self.pi_service.request_root(user) / '.api-request.lock'
        with path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                return method(self, user, *args, **kwargs)
            except OSError:
                raise AgentUnavailableError('Unable to persist the agent request.') from None
    return wrapped


def request_agent_shutdown():
    """Notify runs without waiting on the registry lock in a signal handler."""
    global _SHUTTING_DOWN
    if _SHUTTING_DOWN:
        return
    _SHUTTING_DOWN = True
    for item in list(_ACTIVE_RUNS.values()):
        item['stop_event'].set()


def stop_agent_runs():
    """Cancel runs before daemon threads die, then clean up their subprocess groups."""
    request_agent_shutdown()
    with _SERVICE_LOCK:
        active = list(_ACTIVE_RUNS.values())
    deadline = time.monotonic() + 8
    for item in active:
        thread = item.get('thread')
        if thread and thread is not threading.current_thread():
            thread.join(timeout=max(0, deadline - time.monotonic()))
    for item in active:
        process = item.get('process')
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 2
    for item in active:
        thread = item.get('thread')
        if thread and thread is not threading.current_thread():
            thread.join(timeout=max(0, deadline - time.monotonic()))


atexit.register(stop_agent_runs)


def iso_now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def to_iso(ts):
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000 if ts > 100000000000 else ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    if isinstance(ts, str):
        return ts
    return iso_now()


class AgentAPIError(Exception):
    status_code = 422
    error = 'invalid_request'
    retryable = False

    def __init__(self, message=None, retryable=None):
        super().__init__(message)
        self.message = message or self.error
        if retryable is not None:
            self.retryable = retryable


class SessionNotFoundError(AgentAPIError):
    status_code = 404
    error = 'session_not_found'


class RunActiveError(AgentAPIError):
    status_code = 409
    error = 'run_active'


class IdempotencyConflictError(AgentAPIError):
    status_code = 409
    error = 'idempotency_conflict'


class EventHistoryExpiredError(AgentAPIError):
    status_code = 410
    error = 'event_history_expired'


class InvalidSettingsError(AgentAPIError):
    status_code = 422
    error = 'invalid_settings'


class InvalidMessageError(AgentAPIError):
    status_code = 422
    error = 'invalid_message'


class AgentUnavailableError(AgentAPIError):
    status_code = 503
    error = 'agent_unavailable'
    retryable = True


class UnauthorizedError(AgentAPIError):
    status_code = 401
    error = 'unauthorized'
    retryable = True


class AgentService:
    def __init__(self, config):
        self.config = config
        self.pi_service = AgentBackend(config)

    def _read_idempotency(self, file_path):
        if not file_path.is_file():
            return {}
        try:
            data = json.loads(file_path.read_text(encoding='utf-8'))
            now = time.time()
            # Prune records older than 24h (86400s)
            valid = {k: v for k, v in data.items() if now - v.get('timestamp', 0) < 86400}
            return valid
        except Exception:
            return {}

    def _write_idempotency(self, file_path, data):
        _atomic_write(file_path, json.dumps(data, ensure_ascii=False))

    def _check_idempotency(self, file_path, client_request_id, payload_str):
        if client_request_id is None:
            return None
        if not isinstance(client_request_id, str) or not 1 <= len(client_request_id) <= 200:
            raise AgentAPIError('client_request_id must be a string of 1–200 characters.')
        records = self._read_idempotency(file_path)
        if client_request_id in records:
            entry = records[client_request_id]
            req_hash = hashlib.sha256(payload_str.encode('utf-8')).hexdigest()
            if entry.get('hash') == req_hash:
                return entry.get('response'), entry.get('status_code', 200)
            raise IdempotencyConflictError('client_request_id was already used with different input.')
        return None

    def _save_idempotency(self, file_path, client_request_id, payload_str, response, status_code=200):
        if not client_request_id:
            return
        records = self._read_idempotency(file_path)
        records[client_request_id] = {
            'hash': hashlib.sha256(payload_str.encode('utf-8')).hexdigest(),
            'timestamp': time.time(),
            'response': response,
            'status_code': status_code
        }
        self._write_idempotency(file_path, records)

    def get_config(self, user, working_directory_id=None):
        if working_directory_id is not None and not isinstance(working_directory_id, str):
            raise InvalidSettingsError('Invalid working_directory_id.')
        try:
            choice, choices = self.pi_service.project_context(user, working_directory_id)
        except ValueError as exc:
            if str(exc) == 'Invalid working_directory_id.':
                raise InvalidSettingsError(str(exc)) from None
            return self._unavailable_config()
        except (OSError, KeyError, TypeError):
            return self._unavailable_config()
        try:
            opts = self.pi_service.options(PiService.login_folder(user.username),
                                           project=choice['path'], include_commands=False)
            models = []
            for model in opts.get('models', []):
                model_id = model['provider'] + '/' + model['id']
                selected = opts
                if model.get('reasoning') and model_id != opts.get('current_model'):
                    selected = self.pi_service.options(PiService.login_folder(user.username), model_id,
                        project=choice['path'], include_commands=False)
                levels = selected.get('levels', []) if model.get('reasoning') else []
                level = selected.get('current_thinking')
                models.append({'id': model_id, 'name': model.get('name') or model_id,
                    'thinking_levels': levels,
                    'default_thinking_level': level if level in levels else levels[0] if levels else None})
            if not models:
                return self._unavailable_config(choices)
            default = next((m for m in models if m['id'] == opts.get('current_model')), models[0])
            return {'agent': {'name': 'Coding Agent', 'kind': 'coding_agent', 'available': True},
                    'models': models, 'working_directories': choices,
                    'defaults': {'model_id': default['id'], 'thinking_level': default['default_thinking_level'],
                                 'working_directory_id': choice['id']},
                    'editable_settings': ['model_id', 'thinking_level'] +
                        (['working_directory_id'] if len(choices) > 1 and not choice['id'].startswith('legacy-') else []),
                    'limits': {'max_message_characters': 20000}}
        except Exception as exc:
            logger.warning('Could not load Coding Agent configuration (%s)', type(exc).__name__)
            return self._unavailable_config(choices)

    @staticmethod
    def _unavailable_config(choices=None):
        return {'agent': {'name': 'Coding Agent', 'kind': 'coding_agent', 'available': False,
                          'unavailable_reason': 'Coding Agent or its approved project is unavailable.'},
                'models': [], 'working_directories': choices or [],
                'defaults': {'model_id': '', 'thinking_level': None, 'working_directory_id': None},
                'editable_settings': [], 'limits': {'max_message_characters': 20000}}

    def _validate_settings(self, user, settings, directory_id=None, normalize=False):
        if not isinstance(settings, dict) or not all(k in settings for k in ('model_id', 'thinking_level', 'working_directory_id')):
            raise InvalidSettingsError('All settings fields (model_id, thinking_level, working_directory_id) are required.')
        if (not isinstance(settings['working_directory_id'], str) or
                (directory_id is not None and settings['working_directory_id'] != directory_id)):
            raise InvalidSettingsError('The project cannot change after conversation creation.')
        model_id = settings['model_id']
        if not isinstance(model_id, str) or '/' not in model_id or len(model_id) > 300:
            raise InvalidSettingsError('Choose a provider/model pair.')
        try:
            choice, choices = self.pi_service.project_context(user, settings['working_directory_id'])
            opts = self.pi_service.options(PiService.login_folder(user.username), model_id,
                project=choice['path'], include_commands=False)
        except ValueError as exc:
            raise InvalidSettingsError('Invalid project or model selection.') from None
        except (OSError, RuntimeError):
            raise AgentUnavailableError('Coding Agent or its approved project is unavailable.') from None
        model = next((m for m in opts['models'] if m['provider'] + '/' + m['id'] == model_id), None)
        if model is None:
            raise InvalidSettingsError('The selected model is not available in Pi.')
        levels = opts['levels'] if model.get('reasoning') else []
        if normalize:
            if not levels:
                settings['thinking_level'] = None
            elif settings['thinking_level'] is None:
                current = opts.get('current_thinking')
                settings['thinking_level'] = current if current in levels else levels[0]
        if (levels and settings['thinking_level'] not in levels) or (not levels and settings['thinking_level'] is not None):
            raise InvalidSettingsError('The selected model does not support this thinking level.')
        return {'working_directories': choices}

    def _authorize_session(self, user, metadata):
        try:
            self.pi_service.authorize_session(user, metadata)
        except (OSError, ValueError, KeyError, TypeError):
            raise InvalidSettingsError('This conversation project is unavailable or no longer approved.') from None

    def _effective_settings(self, user, metadata):
        settings = metadata.get('settings', {})
        return {'model_id': settings.get('model') or settings.get('default_model', ''),
                'thinking_level': (settings.get('thinking') or settings.get('default_thinking') or None) if settings.get('supports_thinking', True) else None,
                'working_directory_id': self.pi_service.directory_id(user, metadata)}

    def _get_active_run_for_session(self, sid, folder):
        with _SERVICE_LOCK:
            active = _ACTIVE_RUNS.get(str(folder))
            if active:
                return active.get('meta')
        active_file = folder / 'active_run.json'
        if not active_file.exists():
            return None
        with (folder / 'run.lock').open('a') as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                try:
                    return json.loads(active_file.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    return None
            # Keep the probe locked until stale metadata is removed. A new run
            # must not start in between and have its marker deleted by this query.
            self._recover_run(folder)
            active_file.unlink(missing_ok=True)
        return None

    @staticmethod
    def _recover_run(folder):
        active_file = folder / 'active_run.json'
        if not active_file.is_file():
            return
        active = json.loads(active_file.read_text())
        directory = folder / 'runs' / active['id']
        path = directory / 'meta.json'
        if not path.is_file():
            return
        metadata = json.loads(path.read_text())
        if metadata.get('status') in ('completed', 'cancelled', 'failed'):
            return
        events = directory / 'events.jsonl'
        records = []
        if events.exists():
            raw = events.read_bytes()
            complete = raw[:raw.rfind(b'\n') + 1]
            records = [json.loads(line) for line in complete.splitlines()]
            if complete != raw:
                with events.open('r+b') as handle:
                    handle.truncate(len(complete))
        terminal = next((e['data'] for e in reversed(records) if e['event'] == 'run_finished'), None)
        if terminal is None:
            transcript = folder / 'messages.jsonl'
            saved = [json.loads(line) for line in transcript.read_text().split('\n') if line.strip()] if transcript.exists() else []
            source = next((m for m in reversed(saved) if m.get('run_id') == active['id'] and m.get('role') == 'assistant'), None)
            text = ''.join(e['data']['delta'] for e in records if e['event'] == 'message_delta')
            web_events = directory / 'web-events.jsonl'
            if web_events.is_file():
                candidate, ended, seen = source, False, set()
                source, text = None, ''
                raw_web = web_events.read_bytes()
                complete_web = raw_web[:raw_web.rfind(b'\n') + 1]
                if raw_web != complete_web:
                    with web_events.open('r+b') as handle:
                        handle.truncate(len(complete_web))
                for line in complete_web.splitlines(keepends=True):
                    if not line.endswith(b'\n'):
                        continue
                    event = json.loads(line)['data']
                    if event.get('type') == 'message_start' and event.get('message', {}).get('role') == 'assistant':
                        source, text, ended = None, '', False
                    elif event.get('type') == 'message_update' and event.get('assistantMessageEvent', {}).get('type') == 'text_delta':
                        if ended:
                            source, text, ended = None, '', False
                        text += event['assistantMessageEvent'].get('delta', '')
                    elif event.get('type') == 'message_end' and event.get('message', {}).get('role') == 'assistant':
                        source, ended = event['message'], True
                        seen.add(source.get('id'))
                if candidate and candidate.get('id') not in seen:
                    source = candidate  # Transcript persisted just before its event was written.
            if source:
                text = AgentService._text(source)
            message = {'id': metadata['assistant_message_id'], 'run_id': active['id'], 'role': 'assistant',
                       'content': text, 'status': 'failed', 'created_at': metadata['created_at']}
            if source is None:
                with transcript.open('a') as handle:
                    handle.write(json.dumps(message, ensure_ascii=False) + '\n')
            metadata['final_message_id'] = source['id'] if source else message['id']
            terminal = {'run_id': active['id'], 'status': 'failed', 'message': message,
                        'error': {'error': 'agent_interrupted', 'message': 'The server stopped before this run finished.',
                                  'retryable': True, 'request_id': active['id']}}
            with events.open('a') as handle:
                handle.write(json.dumps({'id': (records[-1]['id'] if records else 0) + 1,
                    'event': 'run_finished', 'data': terminal}, ensure_ascii=False) + '\n')
        metadata.update(status=terminal['status'], response_status=terminal['status'], completed_at=iso_now(), error=terminal.get('error'))
        _atomic_write(path, json.dumps(metadata, ensure_ascii=False))
        with (directory / 'web-events.jsonl').open('a') as handle:
            if terminal['status'] == 'failed':
                handle.write(json.dumps({'event': 'pi', 'data': {'type': 'error', 'message': 'The server stopped before this run finished.'}}) + '\n')
            handle.write(json.dumps({'event': 'pi', 'data': {'type': 'done'}}) + '\n')

    @staticmethod
    def _recover_if_idle(folder):
        marker = folder / 'active_run.json'
        if not marker.exists():
            return
        with (folder / 'run.lock').open('a') as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            AgentService._recover_run(folder)
            marker.unlink(missing_ok=True)

    def format_session(self, metadata, active_run=None, user=None):
        created_ts = metadata.get('created', time.time())
        updated_ts = metadata.get('updated', created_ts)
        return {
            'id': metadata['id'],
            'title': metadata.get('title', 'New conversation'),
            'created_at': to_iso(created_ts),
            'updated_at': to_iso(updated_ts),
            'settings': self._effective_settings(user, metadata),
            'editable_settings': ['model_id', 'thinking_level'],
            'active_run': active_run
        }

    @_serialize_requests
    def create_session(self, user, client_request_id=None, settings_data=None):
        idem_file = self.pi_service.request_root(user) / 'session_idempotency.json'
        payload_str = json.dumps(settings_data or {}, sort_keys=True)
        cached = self._check_idempotency(idem_file, client_request_id, payload_str)
        if cached is not None:
            return cached[0]
        if settings_data is None:
            config = self.get_config(user)
            if not config['agent']['available']:
                raise AgentUnavailableError('Coding Agent or its approved project is unavailable.')
            settings_data = config['defaults']
        else:
            config = self._validate_settings(user, settings_data)
        choice = next((p for p in config['working_directories'] if p['id'] == settings_data['working_directory_id']), None)
        if not choice or choice['id'].startswith('legacy-'):
            raise InvalidSettingsError('Select an approved coding project for a new conversation.')
        metadata = self.pi_service.create_api_session(user, choice, settings_data)
        res = {'session': self.format_session(metadata, user=user)}
        self._save_idempotency(idem_file, client_request_id, payload_str, res, status_code=201)
        return res

    @staticmethod
    def _page_limit(value):
        try:
            limit = int(value)
        except (TypeError, ValueError, OverflowError):
            raise AgentAPIError('limit must be a positive integer.') from None
        if limit < 1:
            raise AgentAPIError('limit must be a positive integer.')
        return min(limit, 100)

    def list_sessions(self, user, limit=30, cursor=None):
        limit = self._page_limit(limit)
        try:
            for project in self.pi_service.projects(user):
                self.pi_service.import_native(user, project['path'])
        except (OSError, ValueError):
            pass  # Saved history remains readable when project discovery is unavailable.
        sessions_meta = self.pi_service.list(user)

        # Sort by updated_at / created descending
        sessions_meta.sort(key=lambda m: m.get('updated', m.get('created', 0)), reverse=True)

        # Find starting offset by cursor (using session id as cursor)
        start_idx = 0
        if cursor:
            for idx, m in enumerate(sessions_meta):
                if m['id'] == cursor:
                    start_idx = idx + 1
                    break

        page = sessions_meta[start_idx:start_idx + limit]
        next_cursor = page[-1]['id'] if (start_idx + limit < len(sessions_meta) and page) else None

        results = []
        for m in page:
            try:
                f = self.pi_service.folder(user, m['id'])
                active_run = self._get_active_run_for_session(m['id'], f)
            except Exception:
                active_run = None
            results.append(self.format_session(m, active_run=active_run, user=user))

        return {
            'sessions': results,
            'next_cursor': next_cursor
        }

    def get_session_detail(self, user, sid):
        try:
            f = self.pi_service.folder(user, sid)
        except FileNotFoundError:
            raise SessionNotFoundError('Session not found.')

        metadata = self.pi_service.metadata(f)
        active_run = self._get_active_run_for_session(sid, f)
        return {'session': self.format_session(metadata, active_run=active_run, user=user)}

    def update_settings(self, user, sid, settings_data):
        try:
            folder = self.pi_service.folder(user, sid)
        except FileNotFoundError:
            raise SessionNotFoundError('Session not found.') from None
        try:
            lock = self.pi_service.acquire(folder)
        except BusyError:
            raise RunActiveError('Cannot change settings while a run is active.') from None
        try:
            metadata = self.pi_service.metadata(folder)
            self._authorize_session(user, metadata)
            target = settings_data.get('settings', settings_data) if isinstance(settings_data, dict) else None
            self._validate_settings(user, target, self.pi_service.directory_id(user, metadata))
            metadata['settings'].update(model=target['model_id'], thinking=target['thinking_level'] or '', supports_thinking=target['thinking_level'] is not None)
            for key in ('default_model', 'default_thinking'):
                metadata['settings'].pop(key, None)
            self.pi_service.write_metadata(folder, metadata)
            return {'session': self.format_session(metadata, user=user)}
        finally:
            lock.close()

    def get_messages(self, user, sid, limit=50, before=None):
        try:
            f = self.pi_service.folder(user, sid)
        except FileNotFoundError:
            raise SessionNotFoundError('Session not found.')

        limit = self._page_limit(limit)
        active = self._get_active_run_for_session(sid, f)
        raw_history = self.pi_service.history(f)
        metadata = self.pi_service.metadata(f)

        formatted_messages = []
        run_states = {}
        for idx, raw in enumerate(raw_history):
            role = raw.get('role')
            if role not in ('user', 'assistant'):
                continue
            if active and role == 'assistant' and raw.get('run_id') == active['id']:
                continue
            content = raw.get('content', '')
            if isinstance(content, list):
                content = '\n'.join(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text')
            elif not isinstance(content, str):
                content = str(content or '')

            msg_id = raw.get('id') or f"msg-{hashlib.sha256(f'{sid}_{idx}_{role}'.encode()).hexdigest()[:16]}"
            run_id = raw.get('run_id')
            status = raw.get('status') or 'completed'
            if raw.get('run_id') and role == 'assistant':
                if run_id not in run_states:
                    path = f / 'runs' / run_id / 'meta.json'
                    run_states[run_id] = json.loads(path.read_text()) if path.is_file() else {}
                state = run_states[run_id]
                if state.get('final_message_id') == raw.get('id'):
                    msg_id = state['assistant_message_id']
                    status = state['response_status']
            created_at = raw.get('created_at') or to_iso(raw.get('timestamp', metadata.get('created')))

            formatted_messages.append({
                'id': msg_id,
                'run_id': run_id,
                'role': role,
                'content': content,
                'status': status,
                'created_at': created_at
            })

        if active:
            event_path = f / 'runs' / active['id'] / 'events.jsonl'
            partial = ''
            if event_path.is_file():
                for line in event_path.read_bytes().splitlines(keepends=True):
                    if line.endswith(b'\n'):
                        event = json.loads(line)
                        if event['event'] == 'message_delta':
                            partial += event['data']['delta']
            formatted_messages.append({'id': active['assistant_message_id'], 'run_id': active['id'],
                'role': 'assistant', 'content': partial, 'status': 'streaming', 'created_at': active['created_at']})

        # Pagination with `before`
        candidates = formatted_messages
        if before:
            pos = None
            for idx, m in enumerate(formatted_messages):
                if m['id'] == before:
                    pos = idx
                    break
            if pos is not None:
                candidates = formatted_messages[:pos]

        if len(candidates) > limit:
            page = candidates[-limit:]
            next_before = page[0]['id']
        else:
            page = candidates
            next_before = None

        return {
            'messages': page,
            'next_before': next_before
        }

    @_serialize_requests
    def submit_run(self, user, sid, client_request_id, message_obj, web_settings=None):
        try:
            f = self.pi_service.folder(user, sid)
        except FileNotFoundError:
            raise SessionNotFoundError('Session not found.')

        if web_settings is not None and not isinstance(web_settings, dict):
            raise InvalidSettingsError('Settings must be an object.')

        if not isinstance(message_obj, dict) or not isinstance(message_obj.get('content'), str):
            raise InvalidMessageError('Message content must be a non-empty string.')

        content = message_obj['content'].strip()
        if not content:
            raise InvalidMessageError('Message content cannot be empty.')
        if len(content) > (32000 if web_settings is not None else 20000):
            raise InvalidMessageError('Message content exceeds limit of 20000 characters.')

        idem_file = f / 'idempotency.json'
        payload_str = json.dumps({'content': content}, sort_keys=True)
        cached = self._check_idempotency(idem_file, client_request_id, payload_str)
        if cached is not None:
            return cached[0]

        if self._get_active_run_for_session(sid, f):
            raise RunActiveError('This conversation is currently running a request.')

        try:
            lock = self.pi_service.acquire(f)
        except BusyError:
            raise RunActiveError('This conversation is currently running a request.')

        run_id = f"run-{uuid.uuid4().hex}"
        user_msg_id = f"msg-{uuid.uuid4().hex}"
        assistant_msg_id = f"msg-{uuid.uuid4().hex}"
        stop_event = threading.Event()
        active_file = f / 'active_run.json'
        try:
            metadata = self.pi_service.metadata(f)
            self._authorize_session(user, metadata)
            if web_settings is not None:
                try:
                    metadata = self.pi_service.configure(user, f, web_settings)
                except ValueError as exc:
                    raise InvalidSettingsError(str(exc)) from None
                except (OSError, RuntimeError):
                    raise AgentUnavailableError('Coding Agent or its approved project is unavailable.') from None
            effective = self._effective_settings(user, metadata)
            if web_settings is not None and not web_settings.get('thinking'):
                effective['thinking_level'] = None
            if not effective['model_id']:
                config = self.get_config(user, effective['working_directory_id'])
                if not config['agent']['available']:
                    raise AgentUnavailableError('Coding Agent or its approved project is unavailable.')
                effective.update(model_id=config['defaults']['model_id'], thinking_level=config['defaults']['thinking_level'])
            self._validate_settings(user, effective, effective['working_directory_id'], normalize=True)
            metadata['settings'].update(model=effective['model_id'], thinking=effective['thinking_level'] or '', supports_thinking=effective['thinking_level'] is not None)
            self.pi_service.write_metadata(f, metadata)
            run_meta = {
                'id': run_id, 'session_id': sid, 'status': 'queued',
                'user_message_id': user_msg_id, 'assistant_message_id': assistant_msg_id,
                'settings': effective,
                'created_at': iso_now(), 'completed_at': None,
            }
            run_dir = f / 'runs' / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            _atomic_write(run_dir / 'meta.json', json.dumps(run_meta, ensure_ascii=False))
            _atomic_write(active_file, json.dumps(run_meta, ensure_ascii=False))
            self.pi_service.save(f, {'id': user_msg_id, 'role': 'user', 'content': content,
                'run_id': run_id, 'status': 'completed', 'created_at': run_meta['created_at']})
            metadata['updated'] = time.time()
            if metadata['title'] == 'New conversation':
                metadata['title'] = content[:70]
            self.pi_service.write_metadata(f, metadata)
            thread = threading.Thread(target=self._execute_run,
                args=(f, sid, run_meta, content, lock, stop_event), daemon=True)
            res = {'run': dict(run_meta)}
            # Persist before starting execution, under the same request lock as the lookup.
            self._save_idempotency(idem_file, client_request_id, payload_str, res, status_code=202)
            with _SERVICE_LOCK:
                if _SHUTTING_DOWN:
                    raise AgentUnavailableError('The agent worker is shutting down.')
                _ACTIVE_RUNS[str(f)] = {'run_id': run_id, 'meta': run_meta, 'folder': f,
                                     'thread': thread, 'stop_event': stop_event}
                thread.start()
            return res
        except BaseException:
            with _SERVICE_LOCK:
                _ACTIVE_RUNS.pop(str(f), None)
            active_file.unlink(missing_ok=True)
            lock.close()
            if client_request_id:
                records = self._read_idempotency(idem_file)
                records.pop(client_request_id, None)
                self._write_idempotency(idem_file, records)
            raise

    @staticmethod
    def _text(message):
        content = message.get('content', '')
        if isinstance(content, str):
            return content
        return '\n'.join(block.get('text', '') for block in content
                         if isinstance(block, dict) and block.get('type') == 'text')

    def _save_response(self, folder, run_meta, status, last_message, partial_text):
        """Native transcript IDs stay immutable; API IDs are mapped in run metadata."""
        if last_message is None:
            last_message = {'id': run_meta['assistant_message_id'], 'role': 'assistant',
                            'content': partial_text, 'run_id': run_meta['id'],
                            'status': status, 'created_at': run_meta['created_at']}
            self.pi_service.save(folder, last_message)
        return ({'id': run_meta['assistant_message_id'], 'run_id': run_meta['id'], 'role': 'assistant',
                 'content': self._text(last_message), 'status': status,
                 'created_at': last_message['created_at']}, last_message['id'])

    def _execute_run(self, folder, sid, run_meta, message_content, lock, stop_event):
        run_id = run_meta['id']
        run_dir = folder / 'runs' / run_id
        event_counter = 0
        last_message = None
        partial_text = []
        message_ended = False
        terminal_status = 'completed'
        error_obj = None
        user_echo_seen = False

        def emit(event_type, data):
            nonlocal event_counter
            event_counter += 1
            record = {'id': event_counter, 'event': event_type, 'data': data}
            with (run_dir / 'events.jsonl').open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + '\n')

        def emit_raw(event):
            with (run_dir / 'web-events.jsonl').open('a', encoding='utf-8') as handle:
                handle.write(json.dumps({'event': 'pi', 'data': event}, ensure_ascii=False) + '\n')

        def track_process(process):
            with _SERVICE_LOCK:
                active = _ACTIVE_RUNS.get(str(folder))
                if active and active['run_id'] == run_id:
                    active['process'] = process

        try:
            with _SERVICE_LOCK:
                run_meta['status'] = 'running'
            _atomic_write(run_dir / 'meta.json', json.dumps(run_meta, ensure_ascii=False))
            _atomic_write(folder / 'active_run.json', json.dumps(run_meta, ensure_ascii=False))
            emit('status', {'run_id': run_id, 'status': 'running', 'stage': 'thinking'})
            emit_raw({'type': 'api_user_message', 'message': message_content})
            for event in self.pi_service.run(folder, message_content, lock,
                    user_message_meta={'id': run_meta['user_message_id'], 'run_id': run_id,
                                       'status': 'completed', 'created_at': run_meta['created_at']},
                    assistant_message_meta={'run_id': run_id, 'status': 'completed', 'created_at': run_meta['created_at']},
                    release_lock=False, stop_event=stop_event, on_process=track_process, user_message_saved=True):
                kind = event.get('type')
                if kind == 'message_end' and event.get('message', {}).get('role') == 'user':
                    if user_echo_seen:
                        emit_raw({'type': 'api_user_message', 'message': self._text(event['message'])})
                    user_echo_seen = True
                if kind != 'done':
                    emit_raw(event)
                if kind == 'message_start' and event.get('message', {}).get('role') == 'assistant':
                    partial_text = []
                    last_message = None
                    message_ended = False
                elif kind in ('tool_execution_start', 'tool_execution_end', 'auto_retry_start', 'auto_retry_end', 'auto_compaction_start', 'auto_compaction_end'):
                    emit('status', {'run_id': run_id, 'status': 'running', 'stage': 'working'})
                elif kind == 'message_update':
                    update = event.get('assistantMessageEvent', {})
                    if update.get('type') == 'thinking_delta':
                        emit('status', {'run_id': run_id, 'status': 'running', 'stage': 'thinking'})
                    elif update.get('type') == 'text_delta' and update.get('delta'):
                        if message_ended:
                            partial_text = []
                            last_message = None
                            message_ended = False
                        if not partial_text:
                            emit('status', {'run_id': run_id, 'status': 'running', 'stage': 'responding'})
                        partial_text.append(update['delta'])
                        emit('message_delta', {'run_id': run_id, 'message_id': run_meta['assistant_message_id'], 'delta': update['delta']})
                elif kind == 'message_end' and event.get('message', {}).get('role') == 'assistant':
                    last_message = event['message']
                    message_ended = True
                    if last_message.get('errorMessage'):
                        terminal_status = 'failed'
                        error_obj = {'error': 'provider_error', 'message': 'The model provider could not complete this response.',
                                     'retryable': True, 'request_id': run_id}
                elif kind == 'error':
                    terminal_status = 'failed'
                    error_obj = {'error': 'agent_failed', 'message': 'Pi could not complete this response.',
                                 'retryable': True, 'request_id': run_id}
                elif kind == 'status' and event.get('message') == 'Stopped.':
                    terminal_status = 'cancelled'
        except Exception as exc:
            terminal_status = 'failed'
            error_obj = {'error': 'agent_failed', 'message': 'Pi could not complete this response.', 'retryable': True, 'request_id': run_id}
        finally:
            try:
                if stop_event.is_set() or (folder / 'stop').exists():
                    terminal_status = 'cancelled'
                terminal_msg, source_id = self._save_response(folder, run_meta, terminal_status, last_message, ''.join(partial_text))
                final_meta = {**run_meta, 'final_message_id': source_id, 'response_status': terminal_status}
                _atomic_write(run_dir / 'meta.json', json.dumps(final_meta, ensure_ascii=False))
                payload = {'run_id': run_id, 'status': terminal_status, 'message': terminal_msg}
                if error_obj:
                    payload['error'] = error_obj
                emit('run_finished', payload)
                _atomic_write(run_dir / 'meta.json', json.dumps({**final_meta, 'status': terminal_status,
                    'completed_at': iso_now(), 'error': error_obj}, ensure_ascii=False))
                if terminal_status == 'failed':
                    emit_raw({'type': 'error', 'message': error_obj['message'] if error_obj else 'Pi could not complete this response.'})
                emit_raw({'type': 'done'})
            except Exception as exc:
                logger.error('Failed to finalize agent run (%s)', type(exc).__name__)
            finally:
                with _SERVICE_LOCK:
                    active = _ACTIVE_RUNS.get(str(folder))
                    if active and active['run_id'] == run_id:
                        _ACTIVE_RUNS.pop(str(folder), None)
                (folder / 'active_run.json').unlink(missing_ok=True)
                (folder / 'stop').unlink(missing_ok=True)
                # Keep the conversation locked until transcript, events and state are final.
                lock.close()

    def cancel_run(self, user, sid, run_id):
        try:
            f = self.pi_service.folder(user, sid)
        except FileNotFoundError:
            raise SessionNotFoundError('Session not found.')

        if not isinstance(run_id, str) or not re.fullmatch(r'run-[0-9a-f]{32}', run_id):
            raise SessionNotFoundError('Run not found.')
        active = self._get_active_run_for_session(sid, f)
        if active and active.get('id') == run_id:
            (f / 'stop').touch()
            return {'run': {'id': run_id, 'status': active['status']}}

        run_meta_file = f / 'runs' / run_id / 'meta.json'
        if run_meta_file.is_file():
            try:
                m = json.loads(run_meta_file.read_text(encoding='utf-8'))
                return {'run': {'id': run_id, 'status': m.get('status', 'completed')}}
            except Exception:
                pass

        raise SessionNotFoundError('Run not found.')

    def stream_run_events(self, user, sid, run_id, last_event_id=None, raw=False):
        # This method deliberately returns a generator instead of being one: all
        # validation runs while the route can still return structured JSON errors.
        try:
            folder = self.pi_service.folder(user, sid)
        except FileNotFoundError:
            raise SessionNotFoundError('Session not found.')
        if (not isinstance(run_id, str) or len(run_id) != 36 or not run_id.startswith('run-')
                or any(char not in '0123456789abcdef' for char in run_id[4:])):
            raise SessionNotFoundError('Run not found.')
        directory = folder / 'runs' / run_id
        if not directory.is_dir():
            raise SessionNotFoundError('Run not found.')
        self._get_active_run_for_session(sid, folder)
        metadata = json.loads((directory / 'meta.json').read_text(encoding='utf-8'))
        if metadata.get('completed_at'):
            completed = datetime.fromisoformat(metadata['completed_at'].replace('Z', '+00:00'))
            if (datetime.now(timezone.utc) - completed).total_seconds() > 86400:
                raise EventHistoryExpiredError('Event history for this run has expired.')
        return self._tail_events(directory, last_event_id, raw=raw)

    @staticmethod
    def _wait_for_finalization(directory):
        folder = directory.parent.parent
        while True:
            with (folder / 'run.lock').open('a') as probe:
                try:
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return
                except BlockingIOError:
                    pass
            try:
                active = json.loads((folder / 'active_run.json').read_text())
                if active.get('id') != directory.name:
                    return  # A newer run already acquired the released lock.
            except (OSError, ValueError):
                pass
            time.sleep(.01)

    @staticmethod
    def _tail_events(directory, last_event_id, raw=False):
        events = directory / ('web-events.jsonl' if raw else 'events.jsonl')
        offset = 0
        last_heartbeat = time.monotonic()
        while True:
            if events.exists():
                with events.open('rb') as handle:
                    handle.seek(offset)
                    while line := handle.readline():
                        if not line.endswith(b'\n'):
                            break  # The writer has not finished this record yet.
                        record = json.loads(line)
                        offset = handle.tell()
                        terminal = record['event'] == 'run_finished' or (raw and record['data'].get('type') == 'done')
                        if terminal:
                            AgentService._wait_for_finalization(directory)
                        if raw:
                            yield 'data: ' + json.dumps(record['data'], ensure_ascii=False) + '\n\n'
                        elif last_event_id is None or record['id'] > last_event_id:
                            yield f"id: {record['id']}\nevent: {record['event']}\ndata: {json.dumps(record['data'], ensure_ascii=False)}\n\n"
                        if terminal:
                            return
            AgentService._recover_if_idle(directory.parent.parent)
            metadata = json.loads((directory / 'meta.json').read_text(encoding='utf-8'))
            if metadata.get('status') in ('completed', 'cancelled', 'failed'):
                if not events.exists() or events.stat().st_size == offset:
                    return
                continue  # Drain records appended between the read and metadata check.
            if time.monotonic() - last_heartbeat >= 15:
                yield ': heartbeat\n\n'
                last_heartbeat = time.monotonic()
            time.sleep(.1)
