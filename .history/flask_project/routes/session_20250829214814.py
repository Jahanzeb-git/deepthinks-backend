import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from flask import Blueprint, jsonify, current_app, request
from auth import token_required
from db import get_db_connection

import uuid
from werkzeug.security import generate_password_hash, check_password_hash


session_bp = Blueprint('session_bp', __name__)

@session_bp.route('/session_inc', methods=['GET'])
@token_required
def new_chat_session(current_user):
    user_id = current_user['id']
    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(session_number) FROM conversation_memory WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            new_session = (result[0] or 0) + 1
            cursor.execute(
                "INSERT OR IGNORE INTO conversation_memory (user_id, session_number, last_updated) VALUES (?, ?, ?)",
                (user_id, new_session, datetime.now(timezone.utc).isoformat())
            )
        return jsonify({'message': 'New session started', 'session_number': new_session})
    except Exception as e:
        logging.error(f"Error creating new session for user {user_id}: {e}", exc_info=True)
        return jsonify({'error': 'Could not start a new session'}), 500
    finally:
        conn.close()

@session_bp.route('/history/<int:session_number>', methods=['GET'])
@token_required
def get_full_session_history(current_user, session_number):
    user_id = current_user['id']
    conn = get_db_connection()
    try:
        history_rows = conn.execute(
            "SELECT prompt, response, timestamp FROM chat_history WHERE user_id = ? AND session_number = ? ORDER BY timestamp ASC",
            (user_id, session_number)
        ).fetchall()
        if not history_rows:
            return jsonify({'message': 'Chat session not found or is empty'}), 404
        history = [dict(row) for row in history_rows]
        return jsonify(history)
    except sqlite3.Error as e:
        logging.error(f"Database error fetching session history: {e}", exc_info=True)
        return jsonify({'error': 'Could not retrieve session history'}), 500
    finally:
        conn.close()

@session_bp.route('/history', methods=['GET'])
@token_required
def get_session_history_summary(current_user):
    user_id = current_user['id']
    conn = get_db_connection()
    try:
        query = """
            SELECT
                ch.session_number,
                ch.prompt,
                ch.timestamp
            FROM (
                SELECT
                    session_number,
                    MIN(id) as first_id
                FROM chat_history
                WHERE user_id = ?
                GROUP BY session_number
            ) AS first_chats
            JOIN chat_history AS ch ON ch.id = first_chats.first_id
            ORDER BY ch.session_number DESC;
        """
        history_summary = conn.execute(query, (user_id,)).fetchall()
        summary = [dict(row) for row in history_summary]
        return jsonify(summary)
    except sqlite3.Error as e:
        logging.error(f"Database error fetching session history summary: {e}", exc_info=True)
        return jsonify({'error': 'Could not retrieve session history summary'}), 500
    finally:
        conn.close()

@session_bp.route('/delete_user', methods=['DELETE'])
@token_required
def delete_user(current_user):
    user_id = current_user['id']
    conn = get_db_connection()
    try:
        with conn:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        logging.info(f"User {user_id} and all associated data deleted successfully.")
        return jsonify({'message': 'User account and all associated data deleted successfully'}), 200
    except sqlite3.Error as e:
        logging.error(f"Database error during user deletion: {e}", exc_info=True)
        return jsonify({'message': f'Database error: {str(e)}'}), 500
    finally:
        conn.close()

#----------------------------------------------------------------
# Conversation Sharing
#----------------------------------------------------------------

# POST /session/<session_number>/share
@session_bp.route('/session/<int:session_number>/share', methods=['POST'])
@token_required
def create_share(current_user, session_number):
    """
    Create a shareable token for the authenticated user's session.
    Request JSON can include:
      - expires_in_minutes (int) optional
      - password (string) optional (will be stored hashed)
      - is_public (bool) optional (default True)
    """
    user_id = current_user['id']
    payload = request.get_json() or {}
    expires_in = payload.get('expires_in_minutes')
    password = payload.get('password')
    is_public = 1 if payload.get('is_public', True) else 0

    share_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat() + "Z"
    expires_at = None
    pw_hash = None

    if expires_in:
        expires_at = (datetime.utcnow() + timedelta(minutes=int(expires_in))).isoformat() + "Z"
    if password:
        pw_hash = generate_password_hash(password)

    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO conversation_shares (share_id, user_id, session_number, created_at, expires_at, password_hash, is_public, revoked) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (share_id, user_id, session_number, created_at, expires_at, pw_hash, is_public)
        )
        conn.commit()
    except Exception as e:
        current_app.logger.exception("Failed to create share")
        return jsonify({"error": "Could not create share"}), 500
    finally:
        conn.close()

    share_url = f"{current_app.config.get('FRONTEND_BASE_URL', '')}/share/{share_id}"
    return jsonify({"share_id": share_id, "share_url": share_url, "expires_at": expires_at}), 201


# GET /conversation-history/share/<share_id>
@session_bp.route('/conversation-history/share/<string:share_id>', methods=['GET'])
def get_shared_conversation(share_id):
    """
    Public endpoint to fetch conversation by share_id.
    Optional query param: password if the share is password protected.
    """
    password = request.args.get('password')
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT user_id, session_number, expires_at, password_hash, revoked, is_public "
            "FROM conversation_shares WHERE share_id = ?",
            (share_id,)
        ).fetchone()
        if not row:
            return jsonify({"message": "Share not found"}), 404

        # check revoked
        if row['revoked']:
            return jsonify({"message": "This share has been revoked"}), 403

        # check expiry
        if row['expires_at']:
            expires_at = datetime.fromisoformat(row['expires_at'].replace("Z", ""))
            if datetime.utcnow() > expires_at:
                return jsonify({"message": "This share has expired"}), 410

        # check password
        pw_hash = row['password_hash']
        if pw_hash:
            if not password or not check_password_hash(pw_hash, password):
                return jsonify({"message": "Password required or incorrect"}), 401

        user_id = row['user_id']
        session_number = row['session_number']

        history_rows = conn.execute(
            "SELECT prompt, response, timestamp FROM chat_history WHERE user_id = ? AND session_number = ? ORDER BY timestamp ASC",
            (user_id, session_number)
        ).fetchall()
        if not history_rows:
            return jsonify({'message': 'Chat session not found or is empty'}), 404

        history = [dict(r) for r in history_rows]
        return jsonify(history)
    except Exception as e:
        current_app.logger.exception("Error fetching shared conversation")
        return jsonify({"error": "Could not retrieve conversation"}), 500
    finally:
        conn.close()
