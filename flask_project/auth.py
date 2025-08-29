import jwt
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import request, jsonify, current_app
from db import get_db_connection

def get_user(email):
    """Retrieves a user by their email from the database."""
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return user

def create_access_token(data: dict):
    """Creates a new JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=current_app.config['ACCESS_TOKEN_EXPIRE_DAYS'])
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, current_app.config['SECRET_KEY'], algorithm=current_app.config['ALGORITHM'])

def token_required(f):
    """Decorator to protect routes with JWT authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            return jsonify({'message': 'Token is missing'}), 401
        try:
            data = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=[current_app.config['ALGORITHM']])
            current_user = get_user(data['sub'])
            if not current_user:
                return jsonify({'message': 'User not found'}), 401
        except jwt.ExpiredSignatureError:
            return jsonify({'message': 'Token has expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'message': 'Token is invalid'}), 401
        return f(current_user, *args, **kwargs)
    return decorated

def optional_token_required(f):
    """Decorator to optionally handle JWT authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        current_user = None
        if token:
            try:
                data = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=[current_app.config['ALGORITHM']])
                current_user = get_user(data['sub'])
            except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
                # Ignore invalid or expired tokens for optional authentication
                pass
        return f(current_user, *args, **kwargs)
    return decorated
