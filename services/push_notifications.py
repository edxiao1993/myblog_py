"""Call send_job_finished inside a Flask app context after committing a successful job."""
import json
import logging
import os
from pathlib import Path
import threading
import time

import httpx
import jwt
from flask import current_app
from database import db
from models.push_device import PushDevice

logger = logging.getLogger(__name__)
# httpx INFO request lines include the APNs device token in the URL.
logging.getLogger('httpx').setLevel(logging.WARNING)
_token_lock = threading.Lock()
_cached_token = None


def _setting(name, default=''):
    return current_app.config.get(name) or os.environ.get(name, default)


def _provider_token():
    global _cached_token
    team, key_id, path = (_setting(name) for name in ('APNS_TEAM_ID', 'APNS_KEY_ID', 'APNS_KEY_PATH'))
    if not all((team, key_id, path)):
        raise ValueError('Configure APNS_TEAM_ID, APNS_KEY_ID, and APNS_KEY_PATH')
    identity = (team, key_id, path)
    now = int(time.time())
    with _token_lock:
        if _cached_token and _cached_token[0] == identity and 0 <= now - _cached_token[2] < 3000:
            return _cached_token[1]
        token = jwt.encode({'iss': team, 'iat': now}, Path(path).read_text(),
                           algorithm='ES256', headers={'kid': key_id})
        _cached_token = (identity, token, now)
        return token


def send_job_finished(user_id, job_id, *, title='Job finished', body='Your scheduled job has finished.'):
    """Notify only this user's devices; return counts, never APNs credentials or tokens.

    Transient failures are counted and retained for a scheduler-level retry. An APNs
    acceptance does not guarantee display on the device. No public send endpoint exists.
    """
    if not isinstance(title, str) or not title.strip() or not isinstance(body, str):
        raise ValueError('A notification title and body are required')
    if not isinstance(job_id, (str, int)) or not str(job_id).strip() or len(str(job_id)) > 200:
        raise ValueError('A job_id of at most 200 characters is required')
    payload = {'aps': {'alert': {'title': title, 'body': body}, 'sound': 'default'},
               'type': 'job_finished', 'job_id': str(job_id), 'user_id': user_id}
    encoded = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    if len(encoded) > 4096:
        raise ValueError('APNs payload exceeds 4096 bytes; send a short summary')
    result = {'sent': 0, 'failed': 0, 'removed': 0}
    devices = PushDevice.query.filter_by(user_id=user_id).all()
    if not devices:
        return result
    headers = {'authorization': 'bearer ' + _provider_token(),
               'apns-topic': _setting('APNS_TOPIC', 'com.kevinchien.BlueNest'),
               'apns-push-type': 'alert', 'apns-priority': '10',
               'apns-expiration': str(int(time.time()) + 86400),
               'content-type': 'application/json'}
    with httpx.Client(http2=True, timeout=10) as client:
        for device in devices:
            registration = {field: getattr(device, field) for field in
                            ('installation_id', 'user_id', 'device_token', 'environment', 'updated_at')}
            host = 'api.sandbox.push.apple.com' if device.environment == 'development' else 'api.push.apple.com'
            try:
                response = client.post(f'https://{host}/3/device/{device.device_token}',
                                       content=encoded, headers=headers)
            except httpx.HTTPError:
                result['failed'] += 1
                logger.warning('Job notification transport failed')
                continue
            if response.status_code == 200:
                result['sent'] += 1
            elif response.status_code == 410:
                removed = PushDevice.query.filter_by(**registration).delete(synchronize_session=False)
                result['removed'] += removed
            else:
                result['failed'] += 1
                logger.warning('Job notification rejected (HTTP %d)', response.status_code)
    db.session.commit()
    logger.info('Job notification delivery: sent=%d failed=%d removed=%d',
                result['sent'], result['failed'], result['removed'])
    return result
