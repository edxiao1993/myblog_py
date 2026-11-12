import atexit
import fcntl
import hashlib
import os
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
        path = self.pi_service.user_root(user) / '.api-request.lock'
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
        return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
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
        self.pi_service = PiService(config)

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

    def get_config(self, user):
        login_name = PiService.login_folder(user.username)
        try:
            opts = self.pi_service.options(login_name)
        except Exception as exc:
            logger.error("Failed to query options from Pi (%s)", type(exc).__name__)
            return {
                'agent': {'name': 'PI', 'available': False, 'unavailable_reason': str(exc)},
                'models': [],
                'working_directories': [],
                'defaults': {
                    'model_id': '',
                    'thinking_level': None,
                    'working_directory_id': 'default'
                },
                'editable_settings': ['model_id', 'thinking_level'],
                'limits': {'max_message_characters': 20000}
            }

        available_levels = opts.get('levels', [])
        formatted_models = []
        for m in opts.get('models', []):
            model_key = f"{m['provider']}/{m['id']}" if m.get('provider') else m['id']
            # If the model specifies reasoning or global thinking levels exist
            has_thinking = bool(m.get('reasoning')) or (len(available_levels) > 1 or (available_levels and available_levels != ['off']))
            levels = available_levels if has_thinking else []
            default_lvl = opts.get('current_thinking') if levels else None
            if not default_lvl and levels:
                default_lvl = 'medium' if 'medium' in levels else levels[0]

            formatted_models.append({
                'id': model_key,
                'name': m.get('name') or model_key,
                'thinking_levels': levels,
                'default_thinking_level': default_lvl
            })

        default_model = opts.get('current_model', '')
        if not default_model and formatted_models:
            default_model = formatted_models[0]['id']

        default_thinking = opts.get('current_thinking', '')
        if not default_thinking and available_levels:
            default_thinking = 'medium' if 'medium' in available_levels else available_levels[0]

        workspace_path = str(self.pi_service.workspace(login_name))

        return {
            'agent': {'name': 'PI', 'available': True},
            'models': formatted_models,
            'working_directories': [
                {'id': 'default', 'name': 'Default workspace', 'path': workspace_path}
            ],
            'defaults': {
                'model_id': default_model,
                'thinking_level': default_thinking or None,
                'working_directory_id': 'default'
            },
            'editable_settings': ['model_id', 'thinking_level'],
            'limits': {'max_message_characters': 20000}
        }

    def _get_active_run_for_session(self, sid, folder):
        with _SERVICE_LOCK:
            active = _ACTIVE_RUNS.get(sid)
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
            active_file.unlink(missing_ok=True)
        return None

    def format_session(self, metadata, active_run=None):
        created_ts = metadata.get('created', time.time())
        updated_ts = metadata.get('updated', created_ts)
        st = metadata.get('settings', {})
        return {
            'id': metadata['id'],
            'title': metadata.get('title', 'New conversation'),
            'created_at': to_iso(created_ts),
            'updated_at': to_iso(updated_ts),
            'settings': {
                'model_id': st.get('model', ''),
                'thinking_level': st.get('thinking', ''),
                'working_directory_id': 'default'
            },
            'active_run': active_run
        }

    @_serialize_requests
    def create_session(self, user, client_request_id=None, settings_data=None):
        idem_file = self.pi_service.user_root(user) / 'session_idempotency.json'
        payload_str = json.dumps(settings_data or {}, sort_keys=True)
        cached = self._check_idempotency(idem_file, client_request_id, payload_str)
        if cached is not None:
            return cached[0]

        internal_settings = None
        if settings_data is not None:
            if not isinstance(settings_data, dict):
                raise InvalidSettingsError('Settings must be an object.')
            req_keys = ('model_id', 'thinking_level', 'working_directory_id')
            if not all(k in settings_data for k in req_keys):
                raise InvalidSettingsError('All settings fields (model_id, thinking_level, working_directory_id) are required.')
            if settings_data.get('working_directory_id') != 'default':
                raise InvalidSettingsError('Invalid working_directory_id.')

            internal_settings = {
                'model': settings_data.get('model_id') or '',
                'thinking': settings_data.get('thinking_level') or '',
                'prompt': 'general'
            }

        try:
            metadata = self.pi_service.create(user, internal_settings)
        except ValueError as exc:
            raise InvalidSettingsError(str(exc))
        except Exception as exc:
            raise AgentUnavailableError(str(exc))

        res = {'session': self.format_session(metadata, active_run=None)}
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
            results.append(self.format_session(m, active_run=active_run))

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
        return {'session': self.format_session(metadata, active_run=active_run)}

    def update_settings(self, user, sid, settings_data):
        try:
            f = self.pi_service.folder(user, sid)
        except FileNotFoundError:
            raise SessionNotFoundError('Session not found.')

        if self._get_active_run_for_session(sid, f):
            raise RunActiveError('Cannot change settings while a run is active.')

        if not isinstance(settings_data, dict):
            raise InvalidSettingsError('Settings must be an object.')

        # Support both {"settings": {...}} and direct {...}
        target = settings_data.get('settings', settings_data)
        if not isinstance(target, dict):
            raise InvalidSettingsError('Settings must be an object.')
        req_keys = ('model_id', 'thinking_level', 'working_directory_id')
        if not all(k in target for k in req_keys):
            raise InvalidSettingsError('All settings fields (model_id, thinking_level, working_directory_id) are required.')
        if target.get('working_directory_id') != 'default':
            raise InvalidSettingsError('Invalid working_directory_id.')

        internal_settings = {
            'model': target.get('model_id') or '',
            'thinking': target.get('thinking_level') or '',
            'prompt': 'general'
        }

        try:
            metadata = self.pi_service.configure(user, f, internal_settings)
        except ValueError as exc:
            raise InvalidSettingsError(str(exc))
        except BusyError:
            raise RunActiveError('Cannot change settings while a run is active.')
        except Exception as exc:
            raise AgentUnavailableError(str(exc))

        return {'session': self.format_session(metadata, active_run=None)}

    def get_messages(self, user, sid, limit=50, before=None):
        try:
            f = self.pi_service.folder(user, sid)
        except FileNotFoundError:
            raise SessionNotFoundError('Session not found.')

        limit = self._page_limit(limit)
        raw_history = self.pi_service.history(f)
        metadata = self.pi_service.metadata(f)

        formatted_messages = []
        run_states = {}
        for idx, raw in enumerate(raw_history):
            role = raw.get('role')
            if role not in ('user', 'assistant'):
                continue
            content = raw.get('content', '')
            if isinstance(content, list):
                content = '\n'.join(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text')
            elif not isinstance(content, str):
                content = str(content or '')

            msg_id = raw.get('id') or f"msg-{hashlib.sha256(f'{sid}_{idx}_{role}'.encode()).hexdigest()[:16]}"
            run_id = raw.get('run_id') or f"run-{hashlib.sha256(f'{sid}_{idx}'.encode()).hexdigest()[:16]}"
            status = raw.get('status') or 'completed'
            if raw.get('run_id') and role == 'assistant':
                if run_id not in run_states:
                    path = f / 'runs' / run_id / 'meta.json'
                    run_states[run_id] = json.loads(path.read_text()) if path.is_file() else {}
                state = run_states[run_id]
                if state.get('final_message_id') == raw.get('id'):
                    msg_id = state['assistant_message_id']
                    status = state['response_status']
            created_at = raw.get('created_at') or to_iso(metadata.get('updated', metadata.get('created')))

            formatted_messages.append({
                'id': msg_id,
                'run_id': run_id,
                'role': role,
                'content': content,
                'status': status,
                'created_at': created_at
            })

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
    def submit_run(self, user, sid, client_request_id, message_obj):
        try:
            f = self.pi_service.folder(user, sid)
        except FileNotFoundError:
            raise SessionNotFoundError('Session not found.')

        if not isinstance(message_obj, dict) or not isinstance(message_obj.get('content'), str):
            raise InvalidMessageError('Message content must be a non-empty string.')

        content = message_obj['content'].strip()
        if not content:
            raise InvalidMessageError('Message content cannot be empty.')
        if len(content) > 20000:
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
            st = metadata.get('settings', {})
            run_meta = {
                'id': run_id, 'session_id': sid, 'status': 'queued',
                'user_message_id': user_msg_id, 'assistant_message_id': assistant_msg_id,
                'settings': {'model_id': st.get('model', ''), 'thinking_level': st.get('thinking', ''),
                             'working_directory_id': 'default'},
                'created_at': iso_now(), 'completed_at': None,
            }
            run_dir = f / 'runs' / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            _atomic_write(run_dir / 'meta.json', json.dumps(run_meta, ensure_ascii=False))
            _atomic_write(active_file, json.dumps(run_meta, ensure_ascii=False))
            thread = threading.Thread(target=self._execute_run,
                args=(f, sid, run_meta, content, lock, stop_event), daemon=True)
            res = {'run': dict(run_meta)}
            # Persist before starting execution, under the same request lock as the lookup.
            self._save_idempotency(idem_file, client_request_id, payload_str, res, status_code=202)
            with _SERVICE_LOCK:
                if _SHUTTING_DOWN:
                    raise AgentUnavailableError('The agent worker is shutting down.')
                _ACTIVE_RUNS[sid] = {'run_id': run_id, 'meta': run_meta, 'folder': f,
                                     'thread': thread, 'stop_event': stop_event}
                thread.start()
            return res
        except BaseException:
            with _SERVICE_LOCK:
                _ACTIVE_RUNS.pop(sid, None)
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
                            'status': status, 'created_at': iso_now()}
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

        def emit(event_type, data):
            nonlocal event_counter
            event_counter += 1
            record = {'id': event_counter, 'event': event_type, 'data': data}
            with (run_dir / 'events.jsonl').open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + '\n')

        def track_process(process):
            with _SERVICE_LOCK:
                active = _ACTIVE_RUNS.get(sid)
                if active and active['run_id'] == run_id:
                    active['process'] = process

        try:
            with _SERVICE_LOCK:
                run_meta['status'] = 'running'
            _atomic_write(run_dir / 'meta.json', json.dumps(run_meta, ensure_ascii=False))
            _atomic_write(folder / 'active_run.json', json.dumps(run_meta, ensure_ascii=False))
            emit('status', {'run_id': run_id, 'status': 'running', 'stage': 'thinking'})
            for event in self.pi_service.run(folder, message_content, lock,
                    user_message_meta={'id': run_meta['user_message_id'], 'run_id': run_id,
                                       'status': 'completed', 'created_at': run_meta['created_at']},
                    assistant_message_meta={'run_id': run_id, 'status': 'completed', 'created_at': run_meta['created_at']},
                    release_lock=False, stop_event=stop_event, on_process=track_process):
                kind = event.get('type')
                if kind == 'message_start' and event.get('message', {}).get('role') == 'assistant':
                    partial_text = []
                    last_message = None
                    message_ended = False
                elif kind in ('tool_execution_start', 'auto_retry_start', 'auto_compaction_start'):
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
                        partial_text.append(update['delta'])
                        emit('message_delta', {'run_id': run_id, 'message_id': run_meta['assistant_message_id'], 'delta': update['delta']})
                elif kind == 'message_end' and event.get('message', {}).get('role') == 'assistant':
                    last_message = event['message']
                    message_ended = True
                    if last_message.get('errorMessage'):
                        terminal_status = 'failed'
                        error_obj = {'error': 'provider_error', 'message': last_message['errorMessage'],
                                     'retryable': True, 'request_id': run_id}
                elif kind == 'error':
                    terminal_status = 'failed'
                    error_obj = {'error': 'agent_failed', 'message': event.get('message', 'Execution failed'),
                                 'retryable': True, 'request_id': run_id}
                elif kind == 'status' and event.get('message') == 'Stopped.':
                    terminal_status = 'cancelled'
        except Exception as exc:
            terminal_status = 'failed'
            error_obj = {'error': 'agent_failed', 'message': str(exc), 'retryable': True, 'request_id': run_id}
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
            except Exception as exc:
                logger.error('Failed to finalize agent run (%s)', type(exc).__name__)
            finally:
                with _SERVICE_LOCK:
                    active = _ACTIVE_RUNS.get(sid)
                    if active and active['run_id'] == run_id:
                        _ACTIVE_RUNS.pop(sid, None)
                (folder / 'active_run.json').unlink(missing_ok=True)
                (folder / 'stop').unlink(missing_ok=True)
                # Keep the conversation locked until transcript, events and state are final.
                lock.close()

    def cancel_run(self, user, sid, run_id):
        try:
            f = self.pi_service.folder(user, sid)
        except FileNotFoundError:
            raise SessionNotFoundError('Session not found.')

        active = self._get_active_run_for_session(sid, f)
        if active and active.get('id') == run_id:
            (f / 'stop').touch()
            return {'run': {'id': run_id, 'status': 'cancelled'}}

        run_meta_file = f / 'runs' / run_id / 'meta.json'
        if run_meta_file.is_file():
            try:
                m = json.loads(run_meta_file.read_text(encoding='utf-8'))
                return {'run': {'id': run_id, 'status': m.get('status', 'completed')}}
            except Exception:
                pass

        return {'run': {'id': run_id, 'status': 'completed'}}

    def stream_run_events(self, user, sid, run_id, last_event_id=None):
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
        metadata = json.loads((directory / 'meta.json').read_text(encoding='utf-8'))
        if metadata.get('completed_at'):
            completed = datetime.fromisoformat(metadata['completed_at'].replace('Z', '+00:00'))
            if (datetime.now(timezone.utc) - completed).total_seconds() > 86400:
                raise EventHistoryExpiredError('Event history for this run has expired.')
        return self._tail_events(directory, last_event_id)

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
    def _tail_events(directory, last_event_id):
        events = directory / 'events.jsonl'
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
                        if record['event'] == 'run_finished':
                            AgentService._wait_for_finalization(directory)
                        if last_event_id is None or record['id'] > last_event_id:
                            yield f"id: {record['id']}\nevent: {record['event']}\ndata: {json.dumps(record['data'], ensure_ascii=False)}\n\n"
                        if record['event'] == 'run_finished':
                            return
            metadata = json.loads((directory / 'meta.json').read_text(encoding='utf-8'))
            if metadata.get('status') in ('completed', 'cancelled', 'failed'):
                if not events.exists() or events.stat().st_size == offset:
                    return
                continue  # Drain records appended between the read and metadata check.
            if time.monotonic() - last_heartbeat >= 15:
                yield ': heartbeat\n\n'
                last_heartbeat = time.monotonic()
            time.sleep(.1)
