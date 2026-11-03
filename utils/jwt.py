import secrets
import jwt
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import request, jsonify, current_app
from database import db


ACCESS_TOKEN_EXPIRY = timedelta(hours=1)
REFRESH_TOKEN_EXPIRY = timedelta(days=30)


def _utcnow():
    """UTC now as naive datetime (for MySQL compatibility)"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def generate_access_token(user_id):
    """Generate a short-lived JWT access token (1 hour)"""
    now = datetime.now(timezone.utc)
    payload = {
        'user_id': user_id,
        'type': 'access',
        'exp': now + ACCESS_TOKEN_EXPIRY,
        'iat': now,
    }
    return jwt.encode(
        payload,
        current_app.config['JWT_SECRET_KEY'],
        algorithm='HS256',
    )


def generate_refresh_token(user_id):
    """Generate a long-lived refresh token (30 days), stored in DB"""
    from models.refresh_token import RefreshToken

    token_value = secrets.token_urlsafe(64)
    refresh_token = RefreshToken(
        token=token_value,
        user_id=user_id,
        expires_at=_utcnow() + REFRESH_TOKEN_EXPIRY,
    )
    db.session.add(refresh_token)
    db.session.commit()

    return token_value


def verify_access_token(token):
    """Verify and decode a JWT access token. Returns User or None."""
    from models.user import User

    try:
        payload = jwt.decode(
            token,
            current_app.config['JWT_SECRET_KEY'],
            algorithms=['HS256'],
        )
        if payload.get('type') != 'access':
            return None
        return db.session.get(User, payload['user_id'])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def verify_refresh_token(token_value):
    """Verify a refresh token against DB. Returns User or None."""
    from models.refresh_token import RefreshToken
    from models.user import User

    refresh_token = db.session.execute(
        db.select(RefreshToken).filter_by(token=token_value, revoked=False)
    ).scalar_one_or_none()

    if not refresh_token or not refresh_token.is_valid():
        return None

    return db.session.get(User, refresh_token.user_id)


def revoke_refresh_token(token_value):
    """Revoke a single refresh token"""
    from models.refresh_token import RefreshToken

    refresh_token = db.session.execute(
        db.select(RefreshToken).filter_by(token=token_value, revoked=False)
    ).scalar_one_or_none()

    if refresh_token:
        refresh_token.revoked = True
        db.session.commit()
        return True
    return False


def revoke_all_user_tokens(user_id):
    """Revoke all refresh tokens for a user (force logout everywhere)"""
    from models.refresh_token import RefreshToken

    db.session.query(RefreshToken).filter_by(
        user_id=user_id, revoked=False
    ).update({'revoked': True})
    db.session.commit()


def jwt_required(f):
    """Decorator to require JWT access token authentication.

    Expects header: Authorization: Bearer <access_token>
    Sets request.current_user to the authenticated User object.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')

        if not auth_header:
            return jsonify({'error': 'Missing Authorization header'}), 401

        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            return jsonify({'error': 'Invalid Authorization header format. Use: Bearer <token>'}), 401

        user = verify_access_token(parts[1])

        if not user:
            return jsonify({'error': 'Invalid or expired token'}), 401

        request.current_user = user
        return f(*args, **kwargs)

    return decorated_function
