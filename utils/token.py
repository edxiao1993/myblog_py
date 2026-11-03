import hashlib
import hmac
import time
import os
from functools import wraps
from flask import request, jsonify, current_app


def get_token_secret():
    """Get the secret key for token generation from environment or config"""
    return os.environ.get('TOKEN_SECRET_KEY', 'dev-token-secret-key')


def generate_token(username: str, password: str, timestamp: int) -> str:
    """
    Generate an HMAC-SHA256 token that cannot be decrypted.
    
    Token format: base64(username:timestamp:hmac_signature)
    
    Args:
        username: The username
        password: The user's password (used in HMAC, not stored)
        timestamp: Current timestamp in seconds
    
    Returns:
        A secure token string
    """
    secret = get_token_secret()
    
    # Create message: username:timestamp
    message = f"{username}:{timestamp}"
    
    # Create HMAC using password + secret as the key
    key = f"{password}:{secret}".encode('utf-8')
    
    # Generate HMAC-SHA256 signature
    signature = hmac.new(key, message.encode('utf-8'), hashlib.sha256).hexdigest()
    
    # Token format: username:timestamp:signature (base64 encoded)
    import base64
    token_data = f"{username}:{timestamp}:{signature}"
    token = base64.b64encode(token_data.encode('utf-8')).decode('utf-8')
    
    return token


def verify_token(token: str, password: str, tolerance_seconds: int = 300) -> tuple[bool, str | None]:
    """
    Verify a token by regenerating it and comparing.
    
    Args:
        token: The token to verify
        password: The user's password for HMAC verification (retrieved from DB)
        tolerance_seconds: How old a token can be (default 5 minutes)
    
    Returns:
        Tuple of (is_valid, username or None)
    """
    import base64
    
    try:
        secret = get_token_secret()
        
        # Decode the token
        token_data = base64.b64decode(token.encode('utf-8')).decode('utf-8')
        parts = token_data.split(':')
        
        if len(parts) != 3:
            return False, None
        
        username, timestamp_str, signature = parts
        timestamp = int(timestamp_str)
        
        # Check if token is too old
        current_time = int(time.time())
        if current_time - timestamp > tolerance_seconds:
            return False, None
        
        # Regenerate the token and compare signatures
        message = f"{username}:{timestamp}"
        key = f"{password}:{secret}".encode('utf-8')
        expected_signature = hmac.new(key, message.encode('utf-8'), hashlib.sha256).hexdigest()
        
        # Constant-time comparison to prevent timing attacks
        if hmac.compare_digest(signature, expected_signature):
            return True, username
        else:
            return False, None
            
    except Exception:
        return False, None


def verify_token_with_db(token: str, tolerance_seconds: int = 300) -> tuple[bool, str | None, str | None]:
    """
    Verify a token by extracting username and looking up password from database.
    
    Args:
        token: The token to verify
        tolerance_seconds: How old a token can be (default 5 minutes)
    
    Returns:
        Tuple of (is_valid, username or None, error_message or None)
    """
    from models.user import User
    from database import db
    import base64
    
    try:
        # First, decode token to get username (without verifying signature yet)
        token_data = base64.b64decode(token.encode('utf-8')).decode('utf-8')
        parts = token_data.split(':')
        
        if len(parts) != 3:
            return False, None, 'Invalid token format'
        
        username, timestamp_str, _ = parts
        timestamp = int(timestamp_str)
        
        # Check if token is too old
        current_time = int(time.time())
        if current_time - timestamp > tolerance_seconds:
            return False, None, 'Token expired'
        
        # Get user from database to retrieve password
        user = db.session.execute(
            db.select(User).filter_by(username=username)
        ).scalar_one_or_none()
        
        if not user:
            return False, None, 'User not found'
        
        # Get the plain password (note: this requires storing reversible password or using a different approach)
        # Since Flask-Login uses password hashes, we need to store a separate API token password
        # For this implementation, we'll use the password_hash directly
        # IMPORTANT: This assumes you have a way to get the original password
        # In practice, you might want to store a separate API secret for each user
        
        # For now, we'll need to modify the User model to store an API secret
        # See the migration note below
        if hasattr(user, 'api_secret') and user.api_secret:
            api_secret = user.api_secret
        else:
            return False, None, 'User has no API secret configured'
        
        # Now verify the token with the retrieved secret
        return verify_token(token, api_secret, tolerance_seconds)
        
    except Exception as e:
        return False, None, f'Verification error: {str(e)}'


def token_required(f):
    """
    Decorator to protect routes with token authentication.
    Expects token in X-Token header only.
    Password/API secret is retrieved from database for verification.
    
    Note: This is a simple implementation. In production:
    - Always use HTTPS
    - Consider using short-lived tokens with refresh mechanism
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('X-Token')
        
        if not token:
            return jsonify({'error': 'Token is missing'}), 401
        
        is_valid, username, error = verify_token_with_db(token)
        
        if not is_valid:
            return jsonify({'error': error or 'Invalid or expired token'}), 401
        
        # Get user from database
        from models.user import User
        from database import db
        
        user = db.session.execute(
            db.select(User).filter_by(username=username)
        ).scalar_one_or_none()
        
        if not user:
            return jsonify({'error': 'User not found'}), 401
        
        # Store user info in request context for use in the route
        request.current_user = user
        request.current_username = username
        return f(*args, **kwargs)
    
    return decorated
