import sqlite3
import logging
from flask import current_app

def get_db_connection():
    """Establishes a connection to the database."""
    conn = sqlite3.connect(current_app.config['DATABASE'], detect_types=sqlite3.PARSE_DECLTYPES)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the database schema."""
    conn = get_db_connection()
    cursor = conn.cursor()
    logging.info("Initializing database with schema...")

    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        email TEXT UNIQUE NOT NULL,
        password TEXT,
        profile_picture TEXT
    );
    CREATE TABLE IF NOT EXISTS api_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        endpoint TEXT,
        model TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS conversation_memory (
        user_id INTEGER NOT NULL,
        session_number INTEGER NOT NULL,
        summary_json TEXT,
        history_buffer TEXT,
        last_updated TEXT NOT NULL,
        PRIMARY KEY (user_id, session_number)
    );
    CREATE TABLE IF NOT EXISTS user_settings (
        user_id INTEGER PRIMARY KEY,
        system_prompt TEXT,
        temperature REAL,
        top_p REAL,
        what_we_call_you TEXT,
        theme TEXT DEFAULT 'Light',
        together_api_key TEXT,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        session_number INTEGER NOT NULL,
        prompt TEXT NOT NULL,
        response TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS unauthorized_request_counts (
        session_id TEXT PRIMARY KEY,
        request_count INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS token_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        model TEXT NOT NULL,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        raw_timestamp INTEGER NOT NULL,
        timestamp_iso TEXT NOT NULL,
        session_id TEXT,
        meta_json TEXT,
        api_key_identifier TEXT DEFAULT '_default',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_token_usage_user_time ON token_usage (user_id, raw_timestamp DESC);
    CREATE TABLE IF NOT EXISTS conversation_shares (
        share_id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        session_number INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT,
        password_hash TEXT,
        is_public INTEGER DEFAULT 1,
        revoked INTEGER DEFAULT 0,
        meta_json TEXT,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_shares_user_session ON conversation_shares (user_id, session_number);
    """)
    conn.commit()
    conn.close()
    logging.info("Database initialization complete.")

def get_unauthorized_request_count(session_id):
    conn = get_db_connection()
    count = 0
    try:
        result = conn.execute(
            "SELECT request_count FROM unauthorized_request_counts WHERE session_id = ?",
            (session_id,)
        ).fetchone()
        if result:
            count = result['request_count']
    except sqlite3.Error as e:
        logging.error(f"Error getting unauthorized request count for {session_id}: {e}", exc_info=True)
    finally:
        conn.close()
    return count

def increment_unauthorized_request_count(session_id):
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO unauthorized_request_counts (session_id, request_count) VALUES (?, 1) "
            "ON CONFLICT(session_id) DO UPDATE SET request_count = request_count + 1",
            (session_id,)
        )
        conn.commit()
    except sqlite3.Error as e:
        logging.error(f"Error incrementing unauthorized request count for {session_id}: {e}", exc_info=True)
    finally:
        conn.close()
