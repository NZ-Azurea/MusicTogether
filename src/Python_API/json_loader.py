import copy
import json
import os
import sqlite3
import sys
from threading import RLock


DEFAULT_DB = {
    "music": [],
    "playlist": {},
    "track_metadata": {},
    "media_assets": {},
    "settings": {
        "launcher": {
            "last_username": "",
            "last_ip": "",
        }
    },
}


_DB_LOCK = RLock()


def _base_dir():
    """Return the persistent data directory for source runs and bundled builds."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _legacy_json_path():
    """Return the old JSON DB path used before SQLite migration."""
    return os.path.join(_base_dir(), "db.json")


def _sqlite_path():
    """Return the SQLite DB path."""
    return os.path.join(_base_dir(), "music_together.sqlite3")


def ensure_db_schema(db):
    """Fill missing top-level keys so older payloads keep working."""
    db = copy.deepcopy(db or {})
    db.setdefault("music", [])
    db.setdefault("playlist", {})
    db.setdefault("track_metadata", {})
    db.setdefault("media_assets", {})
    db.setdefault("settings", {})
    db["settings"].setdefault("launcher", {})
    db["settings"]["launcher"].setdefault("last_username", "")
    db["settings"]["launcher"].setdefault("last_ip", "")
    return db


def _connect():
    """Open the SQLite database with WAL enabled."""
    conn = sqlite3.connect(_sqlite_path(), timeout=30, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _initialize_db(conn):
    """Create the app_state table when missing."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            payload TEXT NOT NULL
        )
        """
    )


def _read_legacy_json():
    """Read and validate the legacy JSON database when available."""
    legacy_path = _legacy_json_path()
    if not os.path.exists(legacy_path):
        return None
    try:
        with open(legacy_path, "r", encoding="utf-8") as handle:
            return ensure_db_schema(json.load(handle))
    except (OSError, json.JSONDecodeError):
        return None


def _archive_legacy_json():
    """Rename the legacy JSON file after a successful one-time migration."""
    legacy_path = _legacy_json_path()
    if not os.path.exists(legacy_path):
        return
    migrated_path = legacy_path + ".migrated"
    try:
        if os.path.exists(migrated_path):
            os.remove(migrated_path)
        os.replace(legacy_path, migrated_path)
    except OSError:
        pass


def _persist_state(conn, db):
    """Persist the full application state atomically."""
    payload = json.dumps(ensure_db_schema(db), ensure_ascii=False)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            INSERT INTO app_state (id, payload)
            VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
            """,
            (payload,),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _load_or_initialize(conn):
    """Load the state row, or initialize defaults on first run."""
    row = conn.execute("SELECT payload FROM app_state WHERE id = 1").fetchone()
    if row is not None:
        return ensure_db_schema(json.loads(row[0]))
    default_db = ensure_db_schema(DEFAULT_DB)
    _persist_state(conn, default_db)
    return default_db


def migrate_legacy_json_to_sqlite():
    """Migrate db.json to SQLite once at startup, then archive the legacy file."""
    legacy_db = _read_legacy_json()
    if legacy_db is None:
        return False

    with _DB_LOCK:
        conn = _connect()
        try:
            _initialize_db(conn)
            row = conn.execute("SELECT payload FROM app_state WHERE id = 1").fetchone()
            if row is None:
                _persist_state(conn, legacy_db)
                _archive_legacy_json()
                return True
            _archive_legacy_json()
        finally:
            conn.close()
    return False


def load_db():
    """Load the application state from SQLite."""
    with _DB_LOCK:
        conn = _connect()
        try:
            _initialize_db(conn)
            return _load_or_initialize(conn)
        finally:
            conn.close()


def save_db(db):
    """Save the application state to SQLite atomically."""
    with _DB_LOCK:
        conn = _connect()
        try:
            _initialize_db(conn)
            _persist_state(conn, db)
        finally:
            conn.close()
