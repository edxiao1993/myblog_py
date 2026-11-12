import uuid
from functools import wraps
from flask import Blueprint, Response, current_app, jsonify, request
from utils.jwt import verify_access_token
from services.agent_service import (
    AgentAPIError,
    AgentService,
    InvalidMessageError,
    InvalidSettingsError,
    UnauthorizedError,
)

bp = Blueprint('agent_api', __name__)


def agent_service():
    return AgentService(current_app.config)


def agent_auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            raise UnauthorizedError('Missing Authorization header.')
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            raise UnauthorizedError('Invalid Authorization header format. Expected: Bearer <token>.')
        user = verify_access_token(parts[1])
        if not user:
            raise UnauthorizedError('Invalid or expired access token.')
        request.current_user = user
        return f(*args, **kwargs)
    return decorated


@bp.errorhandler(AgentAPIError)
def handle_agent_error(err):
    req_id = request.headers.get('X-Request-ID') or uuid.uuid4().hex
    return jsonify({
        'error': err.error,
        'message': err.message,
        'retryable': err.retryable,
        'request_id': req_id,
    }), err.status_code


@bp.get('/config')
@agent_auth_required
def get_config():
    svc = agent_service()
    return jsonify(svc.get_config(request.current_user))


@bp.post('/sessions')
@agent_auth_required
def create_session():
    data = request.get_json(silent=True)
    if data is not None and not isinstance(data, dict):
        raise InvalidSettingsError('Request body must be a JSON object.')
    data = data or {}
    client_request_id = data.get('client_request_id')
    settings_data = data.get('settings')
    svc = agent_service()
    result = svc.create_session(request.current_user, client_request_id, settings_data)
    return jsonify(result), 201


@bp.get('/sessions')
@agent_auth_required
def list_sessions():
    limit = request.args.get('limit', 30)
    cursor = request.args.get('cursor')
    svc = agent_service()
    return jsonify(svc.list_sessions(request.current_user, limit, cursor))


@bp.get('/sessions/<session_id>')
@agent_auth_required
def get_session(session_id):
    svc = agent_service()
    return jsonify(svc.get_session_detail(request.current_user, session_id))


@bp.get('/sessions/<session_id>/messages')
@agent_auth_required
def get_messages(session_id):
    limit = request.args.get('limit', 50)
    before = request.args.get('before')
    svc = agent_service()
    return jsonify(svc.get_messages(request.current_user, session_id, limit, before))


@bp.patch('/sessions/<session_id>/settings')
@agent_auth_required
def update_settings(session_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise InvalidSettingsError('Request body must be a JSON object.')
    svc = agent_service()
    return jsonify(svc.update_settings(request.current_user, session_id, data))


@bp.post('/sessions/<session_id>/runs')
@agent_auth_required
def submit_run(session_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise InvalidMessageError('Request body must be a JSON object.')
    client_request_id = data.get('client_request_id')
    message_obj = data.get('message')
    if not isinstance(message_obj, dict):
        raise InvalidMessageError('Request must include a message object with content.')
    svc = agent_service()
    result = svc.submit_run(request.current_user, session_id, client_request_id, message_obj)
    return jsonify(result), 202


@bp.get('/sessions/<session_id>/runs/<run_id>/events')
@agent_auth_required
def stream_events(session_id, run_id):
    last_event_id = None
    last_header = request.headers.get('Last-Event-ID')
    if last_header:
        try:
            last_event_id = int(last_header)
        except ValueError:
            pass

    svc = agent_service()
    return Response(
        svc.stream_run_events(request.current_user, session_id, run_id, last_event_id=last_event_id),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache, no-transform',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


@bp.post('/sessions/<session_id>/runs/<run_id>/cancel')
@agent_auth_required
def cancel_run(session_id, run_id):
    svc = agent_service()
    return jsonify(svc.cancel_run(request.current_user, session_id, run_id)), 200
