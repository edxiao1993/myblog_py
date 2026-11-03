"""Allowlisted Pi diagnostics: never emit prompts, tool output, paths or raw errors."""
import json
import logging
from pathlib import Path
import re
import traceback
from flask import g, has_request_context

logger = logging.getLogger('pi.agent')
logger.addHandler(logging.NullHandler())
FIELDS = {
    'request_id', 'session_id', 'user_id', 'action', 'method', 'status', 'duration_ms',
    'process_id', 'returncode', 'reason', 'error_type', 'errno', 'http_status', 'frames',
    'event_count', 'tool_count', 'tool', 'is_error', 'disposition', 'last_event_age_s',
    'attachment_count', 'stderr_bytes', 'event_type', 'model', 'thinking',
}


def describe_error(error):
    raw = str(error)
    result = {'error_type': type(error).__name__ if isinstance(error, BaseException) else 'PiError'}
    code = re.search(r'\b([45]\d{2})\b', raw)
    if code:
        result['http_status'] = int(code[1])
    lower = raw.lower()
    for words, reason in [
        (('api key', 'credential', 'unauthorized', 'authentication'), 'authentication'),
        (('rate limit', 'too many requests'), 'rate_limit'),
        (('model not found', 'unknown model'), 'model_unavailable'),
        (('timed out', 'timeout'), 'timeout'),
        (('context length', 'context window'), 'context_limit'),
    ]:
        if any(word in lower for word in words):
            result['reason'] = reason
            break
    if isinstance(error, OSError):
        result['errno'] = error.errno
    if isinstance(error, BaseException) and error.__traceback__:
        result['frames'] = [{'file': Path(frame.filename).name, 'line': frame.lineno,
                             'function': frame.name} for frame in traceback.extract_tb(error.__traceback__)[-8:]]
    return result


def agent_context():
    return dict(getattr(g, 'pi_log_context', {})) if has_request_context() else {}


def agent_event(event, level=logging.INFO, **fields):
    fields = {**agent_context(), **fields}
    clean = {key: value for key, value in fields.items() if key in FIELDS and value is not None}
    # IDs/names are structural identifiers, never arbitrary RPC/body strings.
    for key in ('request_id', 'session_id', 'action', 'method', 'tool', 'event_type', 'disposition', 'reason', 'model', 'thinking'):
        if key in clean:
            value = str(clean[key])
            pattern = r'[a-f0-9]{32}' if key in ('request_id', 'session_id') else r'[A-Za-z0-9_.:/-]{1,100}'
            clean[key] = value if re.fullmatch(pattern, value) else 'unknown'
    logger.log(level, json.dumps({'event': event, **clean}, ensure_ascii=True, separators=(',', ':')))
