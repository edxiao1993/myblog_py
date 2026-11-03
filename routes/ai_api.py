from flask import Blueprint, request, jsonify
from utils.jwt import jwt_required
from services.duckdb_service import (
    create_session,
    append_messages,
    get_session_by_id,
    list_sessions,
    delete_session,
    search_messages,
    ArchiveError,
)

bp = Blueprint('ai_api', __name__)


@bp.errorhandler(ArchiveError)
def archive_error(error):
    return jsonify(error=str(error)), error.status


def json_object():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ArchiveError('Request body must be a JSON object')
    return data


def pagination(name, default):
    try:
        value = int(request.args.get(name, default))
        if not 0 <= value <= 9223372036854775807:
            raise ValueError
        return value
    except ValueError:
        raise ArchiveError('Pagination values must be non-negative integers') from None


@bp.route('/sessions', methods=['POST'])
@jwt_required
def api_create_session():
    """
    Create a new AI session with metadata (no messages yet).

    Headers: Authorization: Bearer <access_token>
    Payload:
    {
        "session_id": "optional-uuid",
        "title": "Chat about MCP",
        "model_name": "deepseek-v4-flash",
        "stream": true,
        "response_format": {"type": "json_object"},
        "config": {}
    }
    """
    user = request.current_user
    data = json_object()

    session_obj, err = create_session(user.id, data)
    if err:
        return jsonify({'error': err}), 400

    return jsonify({
        'message': 'AI session created',
        'session': session_obj
    }), 201


@bp.route('/sessions/<string:session_id>/messages', methods=['POST'])
@jwt_required
def api_append_messages(session_id):
    """
    Append one or more messages to an existing session.

    Headers: Authorization: Bearer <access_token>
    Payload:
    {
        "messages": [
            {"role": "system", "content": "You are Kevin..."},
            {"role": "user", "content": "Tell me about MCP"},
            {"role": "assistant", "content": "{\"response_text\":\"...\"}"}
        ]
    }
    """
    user = request.current_user
    data = json_object()
    messages = data.get('messages') or []

    if not messages:
        return jsonify({'error': 'No messages provided'}), 400

    saved, err = append_messages(user.id, session_id, messages)
    if err:
        status = 404 if 'not found' in err.lower() else 403
        return jsonify({'error': err}), status

    return jsonify({
        'message': f'{len(saved)} message(s) appended',
        'messages': saved
    }), 201


@bp.route('/sessions', methods=['GET'])
@jwt_required
def api_list_sessions():
    """
    List AI sessions for the authenticated user.

    Headers: Authorization: Bearer <access_token>
    Query params:
        limit: int (default 50)
        offset: int (default 0)
    """
    user = request.current_user
    limit = pagination('limit', 50)
    offset = pagination('offset', 0)

    sessions = list_sessions(user.id, limit=limit, offset=offset)
    return jsonify({
        'sessions': sessions,
        'count': len(sessions)
    })


@bp.route('/sessions/<string:session_id>', methods=['GET'])
@jwt_required
def api_get_session(session_id):
    """
    Get full AI session details with all messages.

    Headers: Authorization: Bearer <access_token>
    """
    user = request.current_user
    session_obj, err = get_session_by_id(user.id, session_id)
    if err:
        return jsonify({'error': err}), 404

    return jsonify({'session': session_obj})


@bp.route('/sessions/<string:session_id>', methods=['DELETE'])
@jwt_required
def api_delete_session(session_id):
    """
    Delete an AI session and all its messages.

    Headers: Authorization: Bearer <access_token>
    """
    user = request.current_user
    success, err = delete_session(user.id, session_id)
    if not success:
        return jsonify({'error': err}), 404 if err == 'Session not found' else 403

    return jsonify({'message': 'AI session deleted successfully'})


@bp.route('/sessions/search', methods=['GET'])
@jwt_required
def api_search_sessions():
    """
    Search across AI session messages.

    Headers: Authorization: Bearer <access_token>
    Query params:
        q: search query (required)
        type: comma-separated message types (optional, e.g. "user_message,assistant_response")
        limit: int (default 20)
    """
    user = request.current_user
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'error': 'Query parameter "q" is required'}), 400

    type_filter = request.args.get('type', '').strip()
    message_types = [t.strip() for t in type_filter.split(',') if t.strip()] or None
    limit = pagination('limit', 20)

    results = search_messages(user.id, query, message_types=message_types, limit=limit)
    return jsonify({
        'query': query,
        'results': results,
        'count': len(results)
    })
