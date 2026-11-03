import logging
from flask import Blueprint, request, jsonify
from utils.jwt import (
    generate_access_token,
    generate_refresh_token,
    verify_refresh_token,
    revoke_refresh_token,
)
from database import db
from models.user import User

logger = logging.getLogger(__name__)

bp = Blueprint('api_auth', __name__)


@bp.route('/register', methods=['POST'])
def api_register():
    """
    Sign up a new user via API and return access + refresh tokens.

    Request:  {"username": "...", "email": "...", "password": "..."}
    Response: {
        "access_token":  "<jwt>",
        "refresh_token": "<opaque_token>",
        "token_type":    "Bearer",
        "expires_in":    3600,
        "user":          {"id": 1, "username": "...", "email": "..."}
    }
    """
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    email = (data.get('email') or '').strip()
    password = data.get('password') or ''

    if not username or not email or not password:
        return jsonify({'error': 'username, email, and password are required'}), 400

    if User.query.filter_by(username=username).first():
        logger.warning("API registration failed: username '%s' already exists", username)
        return jsonify({'error': 'Username already exists'}), 409

    if User.query.filter_by(email=email).first():
        logger.warning("API registration failed: email '%s' already registered", email)
        return jsonify({'error': 'Email already registered'}), 409

    user = User(username=username, email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    logger.info("API user registered: '%s' (id=%d)", username, user.id)

    access_token = generate_access_token(user.id)
    refresh_token = generate_refresh_token(user.id)

    return jsonify({
        'access_token': access_token,
        'refresh_token': refresh_token,
        'token_type': 'Bearer',
        'expires_in': 3600,
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
        }
    }), 201


@bp.route('/login', methods=['POST'])
def api_login():
    """
    Login and receive access + refresh tokens.

    Request:  {"username": "...", "password": "..."}
    Response: {
        "access_token":  "<jwt>",
        "refresh_token": "<opaque_token>",
        "token_type":    "Bearer",
        "expires_in":    3600,
        "user":          {"id": 1, "username": "...", "email": "..."}
    }
    """
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    if not username or not password:
        return jsonify({'error': 'username and password are required'}), 400

    user = User.query.filter_by(username=username).first()
    if not user or not user.check_password(password):
        logger.warning("API login failed for username '%s'", username)
        return jsonify({'error': 'Invalid username or password'}), 401

    logger.info("API login successful: user '%s' (id=%d)", username, user.id)

    access_token = generate_access_token(user.id)
    refresh_token = generate_refresh_token(user.id)

    return jsonify({
        'access_token': access_token,
        'refresh_token': refresh_token,
        'token_type': 'Bearer',
        'expires_in': 3600,
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
        }
    })


@bp.route('/token/refresh', methods=['POST'])
def refresh_token():
    """
    Exchange a valid refresh token for a new access token.

    Request:  {"refresh_token": "<token>"}
    Response: {"access_token": "<jwt>", "token_type": "Bearer", "expires_in": 3600}
    """
    data = request.get_json(silent=True) or {}
    token_value = data.get('refresh_token')

    if not token_value:
        return jsonify({'error': 'refresh_token is required'}), 400

    user = verify_refresh_token(token_value)
    if not user:
        logger.warning("Token refresh failed: invalid or expired refresh token")
        return jsonify({'error': 'Invalid or expired refresh token'}), 401

    logger.info("Token refreshed for user_id=%d", user.id)
    access_token = generate_access_token(user.id)

    return jsonify({
        'access_token': access_token,
        'token_type': 'Bearer',
        'expires_in': 3600,
    })


@bp.route('/logout', methods=['POST'])
def api_logout():
    """
    Revoke a refresh token (logout).

    Request:  {"refresh_token": "<token>"}
    Response: {"message": "Logged out"}
    """
    data = request.get_json(silent=True) or {}
    token_value = data.get('refresh_token')

    if not token_value:
        return jsonify({'error': 'refresh_token is required'}), 400

    revoke_refresh_token(token_value)
    logger.info("API logout: refresh token revoked")
    return jsonify({'message': 'Logged out'})
