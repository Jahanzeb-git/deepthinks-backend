
# routes/analytics.py
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from flask import Blueprint, request, jsonify
from db import get_db_connection

logger = logging.getLogger(__name__)
analytics_bp = Blueprint("analytics", __name__, url_prefix="/api")

# A constant for the default key identifier
DEFAULT_KEY_IDENTIFIER = "_default"

def ms_to_iso(ms: int) -> str:
    """Convert milliseconds since epoch to ISO8601 UTC string."""
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()
    except Exception:
        return datetime.now(tz=timezone.utc).isoformat()


def _get_email_from_request() -> Optional[str]:
    """
    Prefer header 'X-User-Email' then JSON body 'email' then query param 'email'.
    Returns None if not found.
    """
    # header
    email = request.headers.get("X-User-Email")
    if email:
        return email.strip().lower()
    # json body (may not exist for GET)
    if request.method in ("POST", "PUT", "PATCH"):
        payload = request.get_json(silent=True) or {}
        if isinstance(payload, dict) and payload.get("email"):
            return str(payload.get("email")).strip().lower()
    # fallback to query param
    q_email = request.args.get("email")
    if q_email:
        return q_email.strip().lower()
    return None


def _find_user_id_by_email(email: str) -> Optional[int]:
    """Return users.id for given email or None if not found."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE lower(email) = ?", (email.lower(),))
    row = cur.fetchone()
    conn.close()
    if row:
        return int(row[0])
    return None


def _validate_and_normalize_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Normalize a single token usage item.
    Expected shape:
      { timestamp: <ms int>, model: <str>, inputTokens: <int>, outputTokens: <int> }
    Additional optional: session_id, api_key_identifier
    Returns normalized dict or None if invalid.
    """
    try:
        # timestamps are mandatory in your description
        ts = item.get("timestamp")
        if ts is None:
            return None
        ts = int(ts)

        model = item.get("model")
        if not model:
            return None
        model = str(model)

        input_tokens = item.get("inputTokens")
        if input_tokens is None:
            return None
        input_tokens = int(input_tokens)

        output_tokens = item.get("outputTokens")
        if output_tokens is None:
            return None
        output_tokens = int(output_tokens)

        session_id = item.get("session_id") or item.get("sessionId") or None
        # Use provided identifier or the default one
        api_key_identifier = item.get("api_key_identifier", DEFAULT_KEY_IDENTIFIER)
        meta_json = json.dumps(item, default=str)

        return {
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "raw_timestamp": ts,
            "timestamp_iso": ms_to_iso(ts),
            "session_id": session_id,
            "api_key_identifier": api_key_identifier,
            "meta_json": meta_json,
        }
    except Exception as e:
        logger.exception("Validation error for token_usage item: %s", e)
        return None


@analytics_bp.route("/token-usage", methods=["POST"])
def create_token_usage():
    """
    Inserts one or multiple token-usage records for the user identified by email.
    - Email must be provided via header 'X-User-Email' OR in JSON body field 'email'.
    - Body may be a single object or an array of objects.
    - Each item can have an 'api_key_identifier', defaults to '_default'.
    Returns inserted count.
    """
    email = _get_email_from_request()
    if not email:
        return jsonify({"ok": False, "error": "Missing user email (provide X-User-Email header or JSON 'email' field)"}), 400

    user_id = _find_user_id_by_email(email)
    if user_id is None:
        return jsonify({"ok": False, "error": f"User with email '{email}' not found"}), 404

    payload = request.get_json(force=True, silent=True)
    if payload is None:
        return jsonify({"ok": False, "error": "Invalid or missing JSON body"}), 400

    items = payload if isinstance(payload, list) else [payload]
    normalized: List[Dict[str, Any]] = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            return jsonify({"ok": False, "error": f"Item at index {i} is not an object"}), 400
        v = _validate_and_normalize_item(it)
        if v is None:
            return jsonify({"ok": False, "error": f"Validation failed for item at index {i}"}), 400
        normalized.append(v)

    # Bulk insert
    conn = get_db_connection()
    cur = conn.cursor()
    insert_sql = """
        INSERT INTO token_usage
            (user_id, model, input_tokens, output_tokens, raw_timestamp, timestamp_iso, session_id, api_key_identifier, meta_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = [
        (
            user_id,
            n["model"],
            n["input_tokens"],
            n["output_tokens"],
            n["raw_timestamp"],
            n["timestamp_iso"],
            n["session_id"],
            n["api_key_identifier"],
            n["meta_json"],
        )
        for n in normalized
    ]
    try:
        cur.executemany(insert_sql, params)
        conn.commit()
        inserted = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else len(params)
        return jsonify({"ok": True, "inserted": inserted}), 201
    except Exception as e:
        conn.rollback()
        logger.exception("Database insert failed: %s", e)
        return jsonify({"ok": False, "error": "Database insert failed", "details": str(e)}), 500
    finally:
        conn.close()


@analytics_bp.route("/token-usage", methods=["GET"])
def get_token_usage():
    """
    Returns token usage records for a user (identified by email).
    Query params:
      - email (optional if provided via header X-User-Email)
      - model (optional)
      - since (optional): ms integer, returns records with raw_timestamp >= since
      - limit (optional, default 100)
      - offset (optional)
      - api_key_identifier (optional, defaults to '_default')
    """
    email = _get_email_from_request()
    if not email:
        return jsonify({"ok": False, "error": "Missing user email (provide X-User-Email header or ?email=)"}), 400

    user_id = _find_user_id_by_email(email)
    if user_id is None:
        return jsonify({"ok": False, "error": f"User with email '{email}' not found"}), 404

    model = request.args.get("model", type=str)
    since = request.args.get("since", type=int)
    limit = min(request.args.get("limit", default=100, type=int), 1000)
    offset = request.args.get("offset", default=0, type=int)
    api_key_identifier = request.args.get("api_key_identifier", default=DEFAULT_KEY_IDENTIFIER, type=str)

    sql = """
        SELECT id, model, input_tokens, output_tokens, raw_timestamp, timestamp_iso, session_id, api_key_identifier, meta_json, created_at
        FROM token_usage
        WHERE user_id = ? AND api_key_identifier = ?
    """
    params: List[Any] = [user_id, api_key_identifier]
    if model:
        sql += " AND model = ?"
        params.append(model)
    if since is not None:
        sql += " AND raw_timestamp >= ?"
        params.append(since)

    sql += " ORDER BY raw_timestamp ASC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
        items: List[Dict[str, Any]] = []
        for r in rows:
            try:
                meta = json.loads(r["meta_json"]) if r["meta_json"] else None
            except Exception:
                meta = None
            items.append({
                "id": r["id"],
                "model": r["model"],
                "inputTokens": r["input_tokens"],
                "outputTokens": r["output_tokens"],
                "timestamp": r["raw_timestamp"],
                "timestamp_iso": r["timestamp_iso"],
                "session_id": r["session_id"],
                "api_key_identifier": r["api_key_identifier"],
                "meta": meta,
                "created_at": r["created_at"],
            })
        return jsonify({"ok": True, "count": len(items), "items": items})
    except Exception as e:
        logger.exception("Database read failed: %s", e)
        return jsonify({"ok": False, "error": "Database read failed", "details": str(e)}), 500
    finally:
        conn.close()