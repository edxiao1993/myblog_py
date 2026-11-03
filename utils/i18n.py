"""Small shared English/Simplified Chinese catalog for Jinja and browser UI."""
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from flask import abort, has_request_context, redirect, request, session

ZH_CN = json.loads((Path(__file__).resolve().parents[1] / 'translations/zh-CN.json').read_text())
LANGUAGES = ('en', 'zh-CN')


def get_locale():
    language = request.cookies.get('language', 'en') if has_request_context() else 'en'
    return language if language in LANGUAGES else 'en'


def translate(message, **values):
    result = ZH_CN.get(message, message) if get_locale() == 'zh-CN' else message
    return result.format(**values) if values else result


def init_i18n(app):
    app.jinja_env.globals.update(_=translate, locale=get_locale)

    @app.context_processor
    def language_context():
        return {
            'language_csrf': session.setdefault('language_csrf', secrets.token_hex(32)),
            'i18n_data': {'locale': get_locale(), 'messages': ZH_CN if get_locale() == 'zh-CN' else {}},
        }

    @app.post('/language')
    def i18n_language():
        expected = session.get('language_csrf', '')
        if not expected or not secrets.compare_digest(expected, request.form.get('csrf_token', '')):
            abort(403)
        language = request.form.get('language')
        if language not in LANGUAGES:
            abort(400)
        target = request.form.get('next', '/')
        try:
            parsed = urlsplit(target)
        except ValueError:
            target = '/'
            parsed = urlsplit(target)
        if (parsed.scheme or parsed.netloc or not target.startswith('/') or
                target.startswith('//') or any(char in target for char in ('\\', '\r', '\n'))):
            target = '/'
        response = redirect(target)
        response.set_cookie('language', language, max_age=365 * 24 * 60 * 60,
                            httponly=True, secure=request.is_secure, samesite='Lax')
        return response
