"""Device registration uses the existing mobile Bearer authentication, never cookies."""
from datetime import datetime
import re
from uuid import UUID
from flask import Blueprint, jsonify, request
from database import db
from models.push_device import PushDevice
from utils.jwt import jwt_required

bp = Blueprint('push', __name__)


def _installation(data):
    if not isinstance(data, dict):
        raise ValueError('A JSON object is required')
    try:
        return str(UUID(data.get('installation_id', '')))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('A valid installation_id is required') from None


@bp.route('/devices', methods=['POST'])
@jwt_required
def register_device():
    data = request.get_json(silent=True)
    try:
        installation_id = _installation(data)
        token = data.get('device_token')
        environment = data.get('environment')
        if not isinstance(token, str) or not re.fullmatch(r'[0-9a-fA-F]{2,512}', token) or len(token) % 2:
            raise ValueError('A hexadecimal device_token is required')
        if environment not in ('development', 'production'):
            raise ValueError('environment must be development or production')
    except ValueError as error:
        return jsonify(error=str(error)), 400

    token = token.lower()
    # A reinstall or account switch must not leave this device attached to an old user.
    PushDevice.query.filter(PushDevice.device_token == token,
                            PushDevice.environment == environment,
                            PushDevice.installation_id != installation_id).delete(synchronize_session=False)
    device = db.session.get(PushDevice, installation_id)
    if device is None:
        device = PushDevice(installation_id=installation_id)
        db.session.add(device)
    device.user_id = request.current_user.id
    device.device_token = token
    device.environment = environment
    device.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(registered=True)


@bp.route('/devices', methods=['DELETE'])
@jwt_required
def unregister_device():
    try:
        installation_id = _installation(request.get_json(silent=True))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    PushDevice.query.filter_by(installation_id=installation_id,
                               user_id=request.current_user.id).delete(synchronize_session=False)
    db.session.commit()
    return '', 204
