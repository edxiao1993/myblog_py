import json
import secrets
import logging
import time
import uuid
from functools import wraps

from flask import Blueprint, g, abort, current_app, jsonify, render_template, request, Response, session, stream_with_context, send_file
from werkzeug.utils import secure_filename
from flask_login import current_user, login_required
from services.pi_service import BusyError, PiService
from utils.i18n import translate as _
from utils.agent_logging import agent_event, describe_error

bp = Blueprint('pi_agent', __name__)


@bp.before_request
def log_request():
    g.pi_started = time.monotonic()
    g.pi_log_context = {'request_id': uuid.uuid4().hex, 'action': request.endpoint,
                        'method': request.method, 'session_id': (request.view_args or {}).get('sid')}
    if current_user.is_authenticated:
        g.pi_log_context['user_id'] = current_user.id
    if request.method == 'POST' or request.endpoint == 'pi_agent.options':
        agent_event('request.started')


@bp.after_request
def log_response(response):
    context = getattr(g, 'pi_log_context', {})
    if context:
        response.headers['X-Request-ID'] = context['request_id']
        level = logging.ERROR if response.status_code >= 500 else logging.WARNING if response.status_code >= 400 else logging.INFO
        agent_event('request.response', level, status=response.status_code,
                    duration_ms=round((time.monotonic() - g.pi_started) * 1000))
    return response


@bp.teardown_request
def log_unhandled_error(error):
    if error is not None:
        agent_event('request.failed', logging.ERROR, **describe_error(error))


@bp.errorhandler(FileNotFoundError)
def missing_file(error):
    return jsonify(error='File or conversation workspace not found.'), 404


def authorized(fn):
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if request.method == 'POST':
            expected = session.get('pi_csrf')
            if not expected or not secrets.compare_digest(expected, request.headers.get('X-CSRF-Token', '')):
                abort(403)
        return fn(*args, **kwargs)
    return wrapper


def service():
    return PiService(current_app.config)


def folder(sid):
    try:
        return service().folder(current_user, sid)
    except FileNotFoundError:
        abort(404)


@bp.get('/pi-agent')
@bp.get('/')
@authorized
def page():
    session.setdefault('pi_csrf', secrets.token_hex(32))
    presets = service().prompts(current_user)
    prompt = presets['general']['text']
    return render_template('pi_agent.html', prompt=prompt, csrf=session['pi_csrf'],
                           presets=presets,
                           upload_limit_mb=current_app.config.get('PI_MAX_UPLOAD_BYTES', 200 * 1024 * 1024) // (1024 * 1024))


@bp.route('/api/pi/prompts', methods=['GET', 'POST'])
@authorized
def prompts():
    agent = service()
    if request.method == 'GET':
        return jsonify(agent.prompts(current_user))
    try:
        return jsonify(agent.create_prompt(current_user, request.get_json(silent=True))), 201
    except ValueError as exc:
        return jsonify(error=_(str(exc))), 400


@bp.route('/api/pi/sessions', methods=['GET', 'POST'])
@authorized
def sessions():
    agent = service()
    if request.method == 'POST':
        try:
            return jsonify(agent.create(current_user, request.get_json(silent=True) or {})), 201
        except ValueError as exc:
            agent_event('request.rejected', logging.WARNING, **describe_error(exc))
            return jsonify(error=str(exc)), 400
        except (OSError, RuntimeError) as exc:
            agent_event('request.rejected', logging.ERROR, **describe_error(exc))
            return jsonify(error=str(exc)), 503
    return jsonify(agent.list(current_user))


@bp.get('/api/pi/sessions/<sid>')
@authorized
def history(sid):
    return jsonify(service().history(folder(sid)))


@bp.post('/api/pi/sessions/<sid>/stop')
@authorized
def stop(sid):
    (folder(sid) / 'stop').touch()
    agent_event('run.stop_requested', session_id=sid)
    return jsonify(ok=True)


@bp.post('/api/pi/sessions/<sid>/messages')
@authorized
def message(sid):
    data = request.get_json(silent=True) or {}
    text = data.get('message') if isinstance(data, dict) else None
    if not isinstance(text, str) or not text.strip() or len(text) > 32000:
        return jsonify(error='Enter a message of 1–32000 characters.'), 400
    agent = service()
    path = folder(sid)
    try:
        lock = agent.acquire(path)
    except BusyError as exc:
        agent_event('request.rejected', logging.WARNING, **describe_error(exc))
        return jsonify(error=str(exc)), 409

    try:
        if 'settings' in data:
            agent.configure(current_user, path, data['settings'])
        attachments = data.get('attachments', [])
        if not isinstance(attachments, list) or len(attachments) > 20:
            raise ValueError('Attach up to 20 uploaded files.')
        for name in attachments:
            if not isinstance(name, str) or not name.startswith('uploads/'):
                raise ValueError('Invalid attachment.')
            agent.file_path(path, name)
    except (ValueError, FileNotFoundError) as exc:
        agent_event('request.rejected', logging.WARNING, **describe_error(exc))
        lock.close()
        return jsonify(error=str(exc) or 'Attachment not found.'), 400
    except (OSError, RuntimeError) as exc:
        agent_event('request.rejected', logging.ERROR, **describe_error(exc))
        lock.close()
        return jsonify(error=str(exc)), 503

    @stream_with_context
    def generate():
        started = time.monotonic()
        outcome = 'disconnected'
        agent_event('stream.opened')
        try:
            for event in agent.run(path, text.strip(), lock, attachments):
                if event.get('type') == 'done':
                    outcome = 'completed'
                elif event.get('type') == 'error':
                    outcome = 'failed'
                yield 'data: ' + json.dumps(event, ensure_ascii=False) + '\n\n'
        finally:
            agent_event('stream.closed', logging.WARNING if outcome == 'disconnected' else logging.INFO,
                        reason=outcome, duration_ms=round((time.monotonic() - started) * 1000))
            lock.close()

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no'})


@bp.post('/api/pi/sessions/<sid>/append')
@authorized
def append_message(sid):
    data = request.get_json(silent=True)
    text = data.get('message') if isinstance(data, dict) else None
    if not isinstance(text, str) or not text.strip() or len(text) > 32000:
        return jsonify(error='Enter a message of 1–32000 characters.'), 400
    try:
        service().append(folder(sid), text.strip())
    except BusyError as exc:
        return jsonify(error=_(str(exc))), 409
    return jsonify(ok=True), 202


@bp.post('/api/pi/sessions/<sid>/archive')
@authorized
def archive(sid):
    from services.duckdb_service import create_session, ArchiveError
    path = folder(sid)
    agent = service()
    try:
        lock = agent.acquire(path)
    except BusyError as exc:
        agent_event('request.rejected', logging.WARNING, **describe_error(exc))
        return jsonify(error=str(exc)), 409
    try:
        messages = agent.history(path)
        if not messages:
            return jsonify(error='This conversation has no messages yet.'), 400
        metadata = agent.metadata(path)
        snapshot = [{'role': 'system', 'content': metadata['prompt_text']}] if metadata['prompt_text'] else []
        for item in messages:
            snapshot.append({'role': item['role'], 'content': item.get('content', '')})
        archived, error = create_session(current_user.id, {
            'title': metadata['title'], 'model_name': metadata['settings']['model'] or current_app.config['PI_MODEL'] or 'Pi configured model',
            'config': {'source': 'pi', 'pi_session_id': sid, 'settings': metadata['settings']},
        }, messages=snapshot)
        if error:
            return jsonify(error=error), 500
        return jsonify(url='/ai-sessions/' + archived['session_id'])
    except ArchiveError as exc:
        return jsonify(error=_(str(exc))), exc.status
    finally:
        lock.close()


@bp.get('/api/pi/options')
@authorized
def options():
    try:
        agent = service()
        result = agent.options(PiService.login_folder(current_user.username), request.args.get('model', ''))
        result['prompts'] = agent.prompts(current_user)
        return jsonify(result)
    except ValueError as exc:
        agent_event('request.rejected', logging.WARNING, **describe_error(exc))
        return jsonify(error=str(exc)), 400
    except (OSError, RuntimeError) as exc:
        agent_event('request.rejected', logging.ERROR, **describe_error(exc))
        return jsonify(error=str(exc)), 503


@bp.get('/api/pi/sessions/<sid>/detail')
@authorized
def detail(sid):
    path = folder(sid)
    agent = service()
    return jsonify(session=agent.metadata(path), messages=agent.history(path), files=agent.files(path))


@bp.post('/api/pi/sessions/<sid>/settings')
@authorized
def settings(sid):
    path = folder(sid)
    agent = service()
    try:
        lock = agent.acquire(path)
    except BusyError as exc:
        agent_event('request.rejected', logging.WARNING, **describe_error(exc))
        return jsonify(error=str(exc)), 409
    try:
        return jsonify(agent.configure(current_user, path, request.get_json(silent=True)))
    except ValueError as exc:
        agent_event('request.rejected', logging.WARNING, **describe_error(exc))
        return jsonify(error=str(exc)), 400
    except (OSError, RuntimeError) as exc:
        agent_event('request.rejected', logging.ERROR, **describe_error(exc))
        return jsonify(error=str(exc)), 503
    finally:
        lock.close()


@bp.route('/api/pi/sessions/<sid>/files', methods=['GET', 'POST'])
@authorized
def files(sid):
    path = folder(sid)
    agent = service()
    if request.method == 'GET':
        return jsonify(agent.files(path))
    maximum = current_app.config.get('PI_MAX_UPLOAD_BYTES', 200 * 1024 * 1024)
    if request.content_length is None:
        return jsonify(error='Upload requires a content length.'), 411
    if request.content_length > maximum:
        return jsonify(error=_('Upload is too large. Limit: {limit} MB per request.', limit=maximum // (1024 * 1024))), 413
    uploads = request.files.getlist('files')
    if not uploads or len(uploads) > 20:
        return jsonify(error='Choose between 1 and 20 files.'), 400
    try:
        lock = agent.acquire(path)
    except BusyError as exc:
        agent_event('request.rejected', logging.WARNING, **describe_error(exc))
        return jsonify(error=str(exc)), 409
    saved = []
    try:
        root = agent.conversation_files(path)
        for upload in uploads:
            # Unique names preserve previous uploads and avoid overwrite races.
            name = secure_filename(upload.filename or '') or 'attachment'
            name = uuid.uuid4().hex[:12] + '-' + name[-180:]
            destination = root / 'uploads' / name
            with destination.open('xb') as handle:
                saved.append(destination)
                upload.save(handle)
        return jsonify(files=[{'path': p.relative_to(root).as_posix(), 'name': p.name,
                               'size': p.stat().st_size} for p in saved]), 201
    except (OSError, FileNotFoundError):
        for destination in saved:
            destination.unlink(missing_ok=True)
        raise
    finally:
        lock.close()


@bp.get('/api/pi/sessions/<sid>/files/<path:name>')
@authorized
def download(sid, name):
    try:
        path = service().file_path(folder(sid), name)
    except FileNotFoundError:
        abort(404)
    response = send_file(path, as_attachment=True, download_name=path.name,
                         mimetype='application/octet-stream', conditional=True)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'private, no-store'
    return response
