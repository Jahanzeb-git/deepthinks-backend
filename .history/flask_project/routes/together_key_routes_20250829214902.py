# routes/together_key_routes.py
import os
import logging
import base64
import requests
from flask import Blueprint, request, jsonify, current_app
from cryptography.fernet import Fernet, InvalidToken
from db import get_db_connection
from auth import token_required  # uses your provided decorator

user_key_bp = Blueprint("user_key", __name__, url_prefix="/user")

# Config
TOGETHER_VALIDATE_URL = os.getenv("TOGETHER_BASE_URL", "https://api.together.xyz/v1").rstrip("/") + "/chat/completions"
TOGETHER_VALIDATE_MODEL = os.getenv("TOGETHER_VALIDATE_MODEL", "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free")
VALIDATION_TIMEOUT = float(os.getenv("TOGETHER_VALIDATE_TIMEOUT", "50"))

# ---- Encryption helpers ----
def get_fernet():
    fkey = os.getenv("TOGETHER_KEY_ENC_KEY")
    if not fkey:
        logging.error("TOGETHER_KEY_ENC_KEY env var not set")
        raise RuntimeError("Server encryption key not configured (TOGETHER_KEY_ENC_KEY).")
    # Accept both raw bytes and str
    if isinstance(fkey, str):
        fkey = fkey.encode()
    # Validate length/format by trying to construct Fernet
    try:
        return Fernet(fkey)
    except Exception as e:
        logging.error("Invalid Fernet key provided in TOGETHER_KEY_ENC_KEY: %s", e)
        raise

def encrypt_key(plain_secret: str) -> str:
    f = get_fernet()
    token = f.encrypt(plain_secret.encode())
    # store as base64 string
    return token.decode()

def decrypt_key(enc_blob: str) -> str:
    f = get_fernet()
    try:
        plain = f.decrypt(enc_blob.encode())
        return plain.decode()
    except InvalidToken:
        logging.exception("Failed to decrypt user together key - possibly tampered or wrong master key.")
        raise

# ---- DB helpers ----
def set_user_together_key(user_id: int, enc_blob: str):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        # Try update first
        cur.execute("UPDATE user_settings SET together_api_key = ? WHERE user_id = ?", (enc_blob, user_id))
        if cur.rowcount == 0:
            # No row updated -> insert
            cur.execute("INSERT INTO user_settings (user_id, together_api_key) VALUES (?, ?)", (user_id, enc_blob))
        conn.commit()
        logging.info(f"Successfully stored Together API key for user {user_id}")
    finally:
        conn.close()

def get_user_together_key_enc(user_id: int):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT together_api_key FROM user_settings WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()

def delete_user_together_key(user_id: int):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE user_settings SET together_api_key = NULL WHERE user_id = ?", (user_id,))
        conn.commit()
        logging.info(f"Successfully deleted Together API key for user {user_id}")
    finally:
        conn.close()

# ---- Validation helper ----
def validate_together_key(api_key: str):
    """
    Make a light request to Together's chat completions endpoint to validate the API key.
    Returns True if it looks valid, otherwise raises an exception or returns False.
    """
    # Basic format validation
    if not api_key or not isinstance(api_key, str):
        logging.warning("API key validation failed: empty or non-string key")
        return False

    # Trim whitespace and check basic format
    api_key = api_key.strip()
    if len(api_key) < 10:  # Together API keys are typically longer
        logging.warning("API key validation failed: key too short")
        return False

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": TOGETHER_VALIDATE_MODEL,
        "messages": [{"role": "user", "content": "Hello (this is a short validation ping)."}],
        "max_tokens": 5
    }

    try:
        logging.info(f"Validating Together API key (first 8 chars: {api_key[:8]}...)")
        resp = requests.post(TOGETHER_VALIDATE_URL, headers=headers, json=payload, timeout=VALIDATION_TIMEOUT)

        if resp.status_code == 200:
            # Extra check: ensure response has expected keys
            j = resp.json()
            if "choices" in j and isinstance(j["choices"], list):
                logging.info("Together API key validation successful")
                return True
            logging.warning("Together API key validation failed: unexpected response format")
            return False
        else:
            # Log the full response for debugging
            logging.warning("Together validation returned status %s: %s", resp.status_code, resp.text)
            return False

    except requests.exceptions.Timeout:
        logging.exception("Together validation timed out")
        return False
    except Exception as e:
        logging.exception("Unexpected error validating Together key: %s", e)
        return False

# ---- Mask helper ----
def mask_key(secret: str, show_last: int = 4):
    if not secret:
        return None
    if len(secret) <= show_last:
        return "*" * len(secret)
    return "*" * (len(secret) - show_last) + secret[-show_last:]

# ---- Helper to extract user ID ----
def get_user_id_from_current_user(current_user):
    """Extract user ID from current_user (SQLite Row object)"""
    if current_user is None:
        return None

    # current_user is a sqlite3.Row object from auth.py
    try:
        return current_user['id']
    except (KeyError, TypeError):
        # Fallback: try attribute access
        try:
            return getattr(current_user, 'id', None)
        except:
            logging.error(f"Unable to extract user ID from current_user: {type(current_user)}")
            return None

# ---- Routes ----

@user_key_bp.route("/key", methods=["POST"])
@token_required
def set_key(current_user):
    """
    Body: { "api_key": "<together-api-key>" }
    Auth required via token_required decorator which passes current_user
    """
    try:
        body = request.get_json(silent=True) or {}
        api_key = body.get("api_key")

        logging.info(f"Received API key setting request. Body keys: {list(body.keys())}")

        if not api_key or not isinstance(api_key, str):
            logging.warning(f"Invalid API key in request: {type(api_key)} - {repr(api_key)}")
            return jsonify({"message": "api_key required in JSON body as string"}), 400

        # Clean the API key
        api_key = api_key.strip()
        if not api_key:
            return jsonify({"message": "api_key cannot be empty"}), 400

        # Validate key with Together (light request)
        valid = validate_together_key(api_key)
        if not valid:
            return jsonify({"message": "Provided Together API key failed validation. Please check your API key and try again."}), 400

        # Extract user ID
        user_id = get_user_id_from_current_user(current_user)
        if user_id is None:
            logging.error(f"Unable to resolve user id from current_user: {type(current_user)}")
            return jsonify({"message": "Unable to resolve user id from token"}), 500

        # Encrypt + store
        try:
            enc = encrypt_key(api_key)
        except Exception as e:
            logging.exception("Encryption setup error")
            return jsonify({"message": "Server encryption not configured correctly"}), 500

        try:
            set_user_together_key(user_id, enc)
            return jsonify({"message": "Together API key stored successfully"}), 200
        except Exception as e:
            logging.exception("DB error saving together key")
            return jsonify({"message": "Failed to save API key"}), 500

    except Exception as e:
        logging.exception("Unexpected error in set_key")
        return jsonify({"message": "Internal server error"}), 500


@user_key_bp.route("/key", methods=["GET"])
@token_required
def get_key(current_user):
    """
    Returns a masked version of the stored key (do NOT return full key).
    """
    try:
        user_id = get_user_id_from_current_user(current_user)
        if user_id is None:
            logging.error(f"Unable to resolve user id from current_user: {type(current_user)}")
            return jsonify({"message": "Unable to resolve user id from token"}), 500

        enc = get_user_together_key_enc(user_id)
        if not enc:
            return jsonify({"api_key_masked": None, "message": "No Together API key stored"}), 200

        try:
            plain = decrypt_key(enc)
        except Exception as e:
            logging.exception("Failed to decrypt stored API key")
            return jsonify({"api_key_masked": None, "message": "Stored key is corrupted or decryption failed"}), 500

        return jsonify({"api_key_masked": mask_key(plain), "message": "OK"}), 200

    except Exception as e:
        logging.exception("Unexpected error in get_key")
        return jsonify({"message": "Internal server error"}), 500


@user_key_bp.route("/key", methods=["DELETE"])
@token_required
def remove_key(current_user):
    """
    Removes the stored Together API key for the authenticated user.
    """
    try:
        user_id = get_user_id_from_current_user(current_user)
        if user_id is None:
            logging.error(f"Unable to resolve user id from current_user: {type(current_user)}")
            return jsonify({"message": "Unable to resolve user id from token"}), 500

        try:
            delete_user_together_key(user_id)
            return jsonify({"message": "Together API key removed successfully"}), 200
        except Exception as e:
            logging.exception("DB error removing together key")
            return jsonify({"message": "Failed to remove key"}), 500

    except Exception as e:
        logging.exception("Unexpected error in remove_key")
        return jsonify({"message": "Internal server error"}), 500