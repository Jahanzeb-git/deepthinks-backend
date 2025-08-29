import sqlite3
from flask import Blueprint, request, jsonify, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from db import get_db_connection
from auth import create_access_token, get_user

auth_bp = Blueprint('auth_bp', __name__)

@auth_bp.route('/signup', methods=['POST'])
def signup():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')
    username = data.get('username')
    if not email or not password or not username:
        return jsonify({'message': 'Username, email, and password are required'}), 400

    hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
    conn = get_db_connection()
    try:
        with conn:
            conn.execute("INSERT INTO users (username, email, password) VALUES (?, ?, ?)",
                       (username, email, hashed_password))
    except sqlite3.IntegrityError:
        return jsonify({'message': 'Email or username already registered'}), 400
    finally:
        conn.close()

    return jsonify({'message': 'Signup successful. Please log in to continue.'}), 201

@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')
    if not email or not password:
        return jsonify({'message': 'Email and password are required'}), 400

    user = get_user(email)
    if not user or not check_password_hash(user['password'], password):
        return jsonify({'message': 'Invalid credentials'}), 401

    access_token = create_access_token(data={"sub": user['email']})
    return jsonify({
        'access_token': access_token,
        'user': {
            'email': user['email'],
            'username': user['username'],
            'profile_picture': user['profile_picture']
        }
    })

@auth_bp.route('/google-login', methods=['POST'])
def google_login():
    data = request.get_json(force=True)
    token = data.get("token")
    if not token:
        return jsonify({'message': 'Missing token'}), 400

    try:
        idinfo = id_token.verify_oauth2_token(token, google_requests.Request(), current_app.config['GOOGLE_CLIENT_ID'])
        email = idinfo.get("email")
        if not email or not idinfo.get("email_verified"):
            return jsonify({'message': 'Invalid or unverified email token'}), 400

        user = get_user(email)
        profile_picture = idinfo.get("picture")
        if user is None:
            username = idinfo.get("name", email.split("@")[0])
            conn = get_db_connection()
            try:
                with conn:
                    conn.execute("INSERT INTO users (username, email, profile_picture) VALUES (?, ?, ?)", (username, email, profile_picture))
            except sqlite3.IntegrityError:
                 pass # User might have been created in a race condition
            finally:
                conn.close()
            user = get_user(email)
        else:
            # Update profile picture if user already exists and picture is available
            conn = get_db_connection()
            try:
                with conn:
                    conn.execute("UPDATE users SET profile_picture = ? WHERE id = ?", (profile_picture, user['id']))
            except sqlite3.Error as e:
                current_app.logger.error(f"Error updating profile picture for user {user['id']}: {e}")
            finally:
                conn.close()

        access_token = create_access_token(data={"sub": email})
        return jsonify({
            'access_token': access_token,
            'token_type': 'bearer',
            'user': {'email': user['email'], 'username': user['username'], 'profile_picture': profile_picture}
        }), 200

    except ValueError as e:
        return jsonify({'message': 'Invalid token', 'error': str(e)}), 400
