import json

def load_db():
    try:
        with open("db.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        db = {"music": [], "playlist": {}}
        save_db(db)
        return db

def save_db(DB):
    with open("db.json", "w") as f:
        json.dump(DB, f, indent=4)