
import sqlite3
from flask import Blueprint, request, jsonify
from auth import token_required
from db import get_db_connection

settings_bp = Blueprint('settings_bp', __name__)

@settings_bp.route('/settings', methods=['GET'])
@token_required
def get_user_settings(current_user):
    user_id = current_user['id']
    conn = get_db_connection()
    try:
        # Fetch user data and settings in one go
        query = """
            SELECT
                u.username,
                u.email,
                s.temperature,
                s.top_p,
                s.what_we_call_you,
                s.theme,
                s.system_prompt,
                u.profile_picture
            FROM users u
            LEFT JOIN user_settings s ON u.id = s.user_id
            WHERE u.id = ?
        """
        settings = conn.execute(query, (user_id,)).fetchone()

        if not settings:
            return jsonify({"error": "User not found"}), 404

        # Return existing settings or defaults
        response = {
            "username": settings['username'],
            "email": settings['email'],
            "temperature": settings['temperature'] if settings['temperature'] is not None else 0.7,
            "top_p": settings['top_p'] if settings['top_p'] is not None else 1.0,
            "what_we_call_you": settings['what_we_call_you'] or settings['username'],
            "theme": settings['theme'] or 'Light',
            "system_prompt": settings['system_prompt'] or 'You are a helpful assistant.',
            "profile_picture": settings['profile_picture'] or None
        }
        return jsonify(response)

    except sqlite3.Error as e:
        return jsonify({'error': f'Database error: {str(e)}'}), 500
    finally:
        conn.close()

@settings_bp.route('/settings', methods=['PATCH'])
@token_required
def update_user_settings(current_user):
    user_id = current_user['id']
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    conn = get_db_connection()
    try:
        with conn:
            # First, ensure a settings row exists for the user
            conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))

            # Dynamically build the update query
            fields_to_update = []
            values = []
            valid_fields = ['temperature', 'top_p', 'what_we_call_you', 'theme', 'system_prompt']

            for field in valid_fields:
                if field in data:
                    fields_to_update.append(f"{field} = ?")
                    values.append(data[field])

            if not fields_to_update:
                return jsonify({"error": "No valid fields to update"}), 400

            query = f"UPDATE user_settings SET { ', '.join(fields_to_update)} WHERE user_id = ?"
            values.append(user_id)
            conn.execute(query, tuple(values))

        return jsonify({"message": "Settings updated successfully"}), 200

    except sqlite3.Error as e:
        return jsonify({'error': f'Database error: {str(e)}'}), 500
    finally:
        conn.close()
