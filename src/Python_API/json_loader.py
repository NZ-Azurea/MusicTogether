import copy
import json
import os
import sys


DEFAULT_DB = {
    "music": [],
    "playlist": {},
    "settings": {
        "launcher": {
            "last_username": "",
            "last_ip": "",
        }
    },
}


def _db_path():
    """Return the persistent DB path for source runs and packaged builds."""
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(base_dir, "db.json")


def ensure_db_schema(db):
    """Fill missing top-level keys so older DB files keep working."""
    db = copy.deepcopy(db or {})
    db.setdefault("music", [])
    db.setdefault("playlist", {})
    db.setdefault("settings", {})
    db["settings"].setdefault("launcher", {})
    db["settings"]["launcher"].setdefault("last_username", "")
    db["settings"]["launcher"].setdefault("last_ip", "")
    return db

def load_db():
    try:
        with open(_db_path(), "r", encoding="utf-8") as f:
            return ensure_db_schema(json.load(f))
    except FileNotFoundError:
        db = ensure_db_schema(DEFAULT_DB)
        save_db(db)
        return db

def save_db(DB):
    DB = ensure_db_schema(DB)
    with open(_db_path(), "w", encoding="utf-8") as f:
        json.dump(DB, f, indent=4, ensure_ascii=False)
