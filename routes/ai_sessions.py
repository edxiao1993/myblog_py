from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import login_required, current_user

from services.duckdb_service import list_sessions, get_session_by_id

bp = Blueprint('ai_sessions', __name__)


@bp.route('/ai-sessions')
@login_required
def sessions_list():
    sessions = list_sessions(current_user.id)
    return render_template('ai_sessions.html', sessions=sessions)


@bp.route('/ai-sessions/<session_id>')
@login_required
def session_detail(session_id):
    session_obj, err = get_session_by_id(current_user.id, session_id)
    if err:
        flash(err, 'danger')
        return redirect(url_for('ai_sessions.sessions_list'))

    # Use response_text (already extracted by DuckDB service) for display
    for msg in session_obj.get('messages', []):
        if msg.get('role') == 'assistant' and msg.get('response_text'):
            msg['display_content'] = msg['response_text']
        else:
            msg['display_content'] = msg.get('content', '')

    return render_template('ai_session_detail.html', session=session_obj)
