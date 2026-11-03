import os
import json
import uuid
import threading
import fcntl
from contextlib import contextmanager
from datetime import datetime, timezone
import duckdb
from flask import current_app

_lock = threading.Lock()
_global_conn = None


def _utcnow():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


class ArchiveError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _is_dev():
    env = os.environ.get('FLASK_ENV', '').lower()
    app_env = current_app.config.get('ENV', '') if current_app else ''
    debug = os.environ.get('FLASK_DEBUG', '').lower() in ('true', '1')
    debug = debug or (current_app.config.get('DEBUG', False) if current_app else False)
    return env != 'production' and app_env != 'production' and debug


def get_duckdb_connection():
    """Return a connection; persistent callers must hold the database file lock."""
    global _global_conn
    if _is_dev():
        if _global_conn is None:
            _global_conn = duckdb.connect(':memory:')
            _init_schema(_global_conn)
        return _global_conn.cursor()
    path = os.path.realpath(os.environ.get('DUCKDB_PATH', 'data/ai_sessions.duckdb'))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = duckdb.connect(path)
    try:
        _init_schema(conn)
        return conn
    except BaseException:
        conn.close()
        raise


@contextmanager
def _connection():
    # The file lock covers opening, the entire transaction, and closing DuckDB.
    # Keeping a writable connection open would block other WSGI workers.
    with _lock:
        lock_file = None
        conn = None
        try:
            if not _is_dev():
                path = os.path.realpath(os.environ.get('DUCKDB_PATH', 'data/ai_sessions.duckdb'))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                lock_file = open(path + '.lock', 'a')
                fcntl.flock(lock_file, fcntl.LOCK_EX)
            conn = get_duckdb_connection()
            conn.execute('BEGIN TRANSACTION')
            try:
                yield conn
                conn.execute('COMMIT')
            except BaseException:
                conn.execute('ROLLBACK')
                raise
        finally:
            if conn is not None:
                conn.close()
            if lock_file is not None:
                lock_file.close()


def close_duckdb_connection():
    """Close the development database; persistent connections close per operation."""
    global _global_conn
    with _lock:
        if _global_conn is not None:
            _global_conn.close()
            _global_conn = None


def _init_schema(conn):
    """Initializes tables and indexes for AI sessions and messages."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_sessions (
            session_id VARCHAR PRIMARY KEY,
            user_id INTEGER NOT NULL,
            title VARCHAR,
            model_name VARCHAR,
            stream BOOLEAN DEFAULT TRUE,
            response_format JSON,
            config JSON,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_messages (
            message_id VARCHAR PRIMARY KEY,
            session_id VARCHAR NOT NULL,
            seq INTEGER NOT NULL,
            role VARCHAR NOT NULL,
            message_type VARCHAR NOT NULL,
            content TEXT,
            response_text TEXT,
            web_search JSON,
            sources JSON,
            suggestion JSON,
            action JSON,
            token_count INTEGER,
            created_at TIMESTAMP
        );
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ai_sessions_user_id ON ai_sessions(user_id);
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ai_messages_session_id ON ai_messages(session_id);
    """)


def _classify_message(role, content):
    """Auto-classify a message based on its role and content prefix."""
    content_str = json.dumps(content, ensure_ascii=False) if isinstance(content, (dict, list)) else str(content or '')
    content_prefix = content_str[:50]
    if role == 'system':
        if 'COMPACTED_MEMORY' in content_prefix:
            return 'compacted_memory'
        elif 'WEB_SEARCH_RESULTS' in content_prefix:
            return 'search_results'
        else:
            return 'system_prompt'
    elif role == 'user':
        if 'TODAY_CONTEXT' in content_prefix:
            return 'context_injection'
        else:
            return 'user_message'
    elif role == 'assistant':
        return 'assistant_response'
    return 'unknown'


def _extract_assistant_fields(content):
    """Parse assistant JSON content and extract structured fields."""
    result = {
        'response_text': None,
        'web_search': None,
        'sources': None,
        'suggestion': None,
        'action': None,
    }
    if not content:
        return result

    data = None
    if isinstance(content, dict):
        data = content
    else:
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                data = parsed
        except (json.JSONDecodeError, TypeError):
            pass

    if isinstance(data, dict):
        result['response_text'] = data.get('response_text') or None
        result['web_search'] = data.get('web_search') or None
        result['sources'] = data.get('sources') or None
        result['suggestion'] = data.get('suggestion') or None
        result['action'] = data.get('action') or None
    else:
        # Not JSON dict — treat content as plain text response
        result['response_text'] = str(content)
    return result


def _get_next_seq(conn, session_id):
    """Get the next sequence number for a session's messages."""
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), -1) FROM ai_messages WHERE session_id = ?",
        [session_id]
    ).fetchone()
    return row[0] + 1


def _parse_json(val, default):
    """Safely parse a JSON value that may already be a Python object."""
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return default


# --- Session CRUD ---

def create_session(user_id, session_data, messages=None):
    """
    Create a new AI session with metadata. Returns (session_dict, error).

    session_data: {
        "session_id": "optional-uuid",
        "title": "Chat about MCP",
        "model_name": "deepseek-v4-flash",
        "stream": true,
        "response_format": {"type": "json_object"},
        "config": {}
    }
    """
    if not isinstance(session_data, dict):
        raise ArchiveError('Request body must be a JSON object')
    for key in ('session_id', 'title', 'model_name'):
        value = session_data.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ArchiveError('Session identifiers and labels must be non-empty strings')
    if 'stream' in session_data and not isinstance(session_data['stream'], bool):
        raise ArchiveError('stream must be a boolean')
    for key in ('response_format', 'config'):
        if session_data.get(key) is not None and not isinstance(session_data[key], dict):
            raise ArchiveError('Session configuration must be a JSON object')
    if messages is not None:
        _validate_messages(messages)
    session_id = session_data.get('session_id') or uuid.uuid4().hex
    title = session_data.get('title') or 'New Session'
    model_name = session_data.get('model_name') or 'unknown'
    stream = session_data.get('stream', True)
    response_format = json.dumps(session_data.get('response_format') or {})
    config = json.dumps(session_data.get('config') or {})
    now = _utcnow()

    with _connection() as conn:
        existing = conn.execute(
            'SELECT user_id, title, model_name, stream, response_format, config, created_at, updated_at FROM ai_sessions WHERE session_id = ?',
            [session_id]).fetchone()
        if existing:
            if (existing[0] != user_id or existing[1:4] != (title, model_name, stream)
                    or _parse_json(existing[4], {}) != json.loads(response_format)
                    or _parse_json(existing[5], {}) != json.loads(config) or messages is not None):
                raise ArchiveError('Session identity conflict', 409)
            now = str(existing[6])
            updated = str(existing[7])
        else:
            conn.execute("""
            INSERT INTO ai_sessions
                (session_id, user_id, title, model_name, stream, response_format, config, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [session_id, user_id, title, model_name, stream, response_format, config, now, now])
            updated = now
            if messages is not None:
                _append_messages(conn, session_id, messages)

    return {
        'session_id': session_id,
        'user_id': user_id,
        'title': title,
        'model_name': model_name,
        'stream': stream,
        'response_format': _parse_json(response_format, {}),
        'config': _parse_json(config, {}),
        'created_at': now,
        'updated_at': updated,
    }, None


def append_messages(user_id, session_id, messages):
    """
    Append one or more messages to an existing session. Returns (messages_list, error).

    messages: [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
    """
    _validate_messages(messages)
    with _connection() as conn:
        row = conn.execute('SELECT user_id FROM ai_sessions WHERE session_id = ?', [session_id]).fetchone()
        if not row:
            return None, 'Session not found'
        if row[0] != user_id:
            return None, 'Unauthorized'
        return _append_messages(conn, session_id, messages), None


def _validate_messages(messages):
    if not isinstance(messages, list) or not messages:
        raise ArchiveError('messages must be a non-empty array')
    for msg in messages:
        if not isinstance(msg, dict):
            raise ArchiveError('Each message must be a JSON object')
        if msg.get('role', 'user') not in ('system', 'user', 'assistant', 'tool', 'function'):
            raise ArchiveError('Invalid message role')
        if not isinstance(msg.get('content', ''), (str, dict, list)):
            raise ArchiveError('Message content must be text, an object, or an array')
        for key in ('message_id', 'message_type'):
            if key in msg and (not isinstance(msg[key], str) or not msg[key].strip()):
                raise ArchiveError('Message identifiers and types must be non-empty strings')
        tokens = msg.get('token_count')
        if tokens is not None and (type(tokens) is not int or not 0 <= tokens <= 2147483647):
            raise ArchiveError('token_count must be a non-negative integer')
        if 'created_at' in msg:
            try:
                datetime.fromisoformat(msg['created_at'])
            except (TypeError, ValueError):
                raise ArchiveError('created_at must be an ISO 8601 timestamp') from None


def _append_messages(conn, session_id, messages):
    next_seq = _get_next_seq(conn, session_id)
    initial_seq = next_seq
    now = _utcnow()
    saved_messages = []

    for msg in messages:
        msg_id = msg.get('message_id') or uuid.uuid4().hex
        role = msg.get('role', 'user')
        content_raw = msg.get('content', '')
        content_str = json.dumps(content_raw, ensure_ascii=False) if isinstance(content_raw, (dict, list)) else str(content_raw or '')
        message_type = msg.get('message_type') or _classify_message(role, content_raw)
        token_count = msg.get('token_count')
        msg_created = msg.get('created_at') or now
        value = datetime.fromisoformat(msg_created)
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        msg_created = str(value)
        seq = next_seq

        # Extract structured fields for assistant messages
        extracted = {'response_text': None, 'web_search': None,
                     'sources': None, 'suggestion': None, 'action': None}
        if role == 'assistant':
            extracted = _extract_assistant_fields(content_raw)

        existing = conn.execute(
            'SELECT session_id, seq, role, message_type, content, response_text, created_at, token_count FROM ai_messages WHERE message_id = ?',
            [msg_id]).fetchone()
        if existing:
            if (existing[0] != session_id or existing[2:5] != (role, message_type, content_str)
                    or existing[7] != token_count
                    or (msg.get('created_at') and existing[6] != datetime.fromisoformat(msg_created))):
                raise ArchiveError('Message identity conflict', 409)
            saved_messages.append(dict(message_id=msg_id, seq=existing[1], role=existing[2],
                                       message_type=existing[3], content=existing[4],
                                       response_text=existing[5], created_at=str(existing[6])))
            continue
        next_seq += 1
        conn.execute("""
            INSERT INTO ai_messages
                (message_id, session_id, seq, role, message_type, content,
                 response_text, web_search, sources, suggestion, action,
                 token_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            msg_id, session_id, seq, role, message_type, content_str,
            extracted['response_text'],
            json.dumps(extracted['web_search']) if extracted['web_search'] else None,
            json.dumps(extracted['sources']) if extracted['sources'] else None,
            json.dumps(extracted['suggestion']) if extracted['suggestion'] else None,
            json.dumps(extracted['action']) if extracted['action'] else None,
            token_count, msg_created
        ])

        saved_messages.append({
            'message_id': msg_id,
            'seq': seq,
            'role': role,
            'message_type': message_type,
            'content': content_str,
            'response_text': extracted['response_text'],
            'created_at': str(msg_created),
        })

    if next_seq != initial_seq:
        conn.execute(
            "UPDATE ai_sessions SET updated_at = ? WHERE session_id = ?",
            [now, session_id]
        )

    return saved_messages


def get_session_by_id(user_id, session_id):
    """Fetches a complete AI session with all messages ordered by seq."""
    with _connection() as conn:
        row = conn.execute("""
            SELECT session_id, user_id, title, model_name, stream,
                   response_format, config, created_at, updated_at
            FROM ai_sessions
            WHERE session_id = ? AND user_id = ?
        """, [session_id, user_id]).fetchone()

        if not row:
            return None, "Session not found"

        msg_rows = conn.execute("""
            SELECT message_id, seq, role, message_type, content,
                   response_text, web_search, sources, suggestion, action,
                   token_count, created_at
            FROM ai_messages
            WHERE session_id = ?
            ORDER BY seq ASC
        """, [session_id]).fetchall()

    session_obj = {
        'session_id': row[0],
        'user_id': row[1],
        'title': row[2],
        'model_name': row[3],
        'stream': row[4],
        'response_format': _parse_json(row[5], {}),
        'config': _parse_json(row[6], {}),
        'created_at': str(row[7]) if row[7] else None,
        'updated_at': str(row[8]) if row[8] else None,
        'messages': [
            {
                'message_id': m[0],
                'seq': m[1],
                'role': m[2],
                'message_type': m[3],
                'content': m[4],
                'response_text': m[5],
                'web_search': _parse_json(m[6], None),
                'sources': _parse_json(m[7], None),
                'suggestion': _parse_json(m[8], None),
                'action': _parse_json(m[9], None),
                'token_count': m[10],
                'created_at': str(m[11]) if m[11] else None,
            }
            for m in msg_rows
        ]
    }
    return session_obj, None


def list_sessions(user_id, limit=50, offset=0):
    """Lists sessions for a given user with summary stats."""
    with _connection() as conn:
        rows = conn.execute("""
            SELECT
                s.session_id,
                s.title,
                s.model_name,
                s.created_at,
                s.updated_at,
                COUNT(m.message_id) AS message_count
            FROM ai_sessions s
            LEFT JOIN ai_messages m ON s.session_id = m.session_id
            WHERE s.user_id = ?
            GROUP BY s.session_id, s.title, s.model_name, s.created_at, s.updated_at
            ORDER BY s.updated_at DESC
            LIMIT ? OFFSET ?
        """, [user_id, limit, offset]).fetchall()

    return [
        {
            'session_id': r[0],
            'title': r[1],
            'model_name': r[2],
            'created_at': str(r[3]) if r[3] else None,
            'updated_at': str(r[4]) if r[4] else None,
            'message_count': r[5]
        }
        for r in rows
    ]


def delete_session(user_id, session_id):
    """Deletes an AI session and its messages."""
    with _connection() as conn:
        row = conn.execute(
            "SELECT user_id FROM ai_sessions WHERE session_id = ?",
            [session_id]
        ).fetchone()

        if not row:
            return False, "Session not found"
        if row[0] != user_id:
            return False, "Unauthorized"

        conn.execute("DELETE FROM ai_messages WHERE session_id = ?", [session_id])
        conn.execute("DELETE FROM ai_sessions WHERE session_id = ?", [session_id])

    return True, None


def search_messages(user_id, query, message_types=None, limit=20):
    """
    Search across messages using LIKE matching.
    Searches content and response_text fields.

    message_types: optional list like ['user_message', 'assistant_response']
    """
    like_pattern = f'%{query}%'

    type_filter = ""
    params = [user_id, like_pattern, like_pattern]
    if message_types:
        placeholders = ', '.join(['?' for _ in message_types])
        type_filter = f"AND m.message_type IN ({placeholders})"
        params.extend(message_types)

    params.append(limit)

    with _connection() as conn:
        rows = conn.execute(f"""
            SELECT
                m.message_id, m.session_id, m.seq, m.role, m.message_type,
                m.content, m.response_text, m.created_at,
                s.title AS session_title, s.model_name
            FROM ai_messages m
            JOIN ai_sessions s ON m.session_id = s.session_id
            WHERE s.user_id = ?
              AND (m.content ILIKE ? OR m.response_text ILIKE ?)
              {type_filter}
            ORDER BY m.created_at DESC
            LIMIT ?
        """, params).fetchall()

    return [
        {
            'message_id': r[0],
            'session_id': r[1],
            'seq': r[2],
            'role': r[3],
            'message_type': r[4],
            'content': r[5][:200] if r[5] else None,  # truncate for search results
            'response_text': r[6][:200] if r[6] else None,
            'created_at': str(r[7]) if r[7] else None,
            'session_title': r[8],
            'model_name': r[9],
        }
        for r in rows
    ]
