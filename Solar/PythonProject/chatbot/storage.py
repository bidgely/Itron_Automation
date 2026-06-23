import json
import os

SESSION_DIR = "chatbot_state/sessions"


def _session_path(space_id, sender_id):
    os.makedirs(SESSION_DIR, exist_ok=True)
    safe = lambda s: s.replace("/", "_").replace(" ", "_")
    return os.path.join(SESSION_DIR, f"{safe(space_id)}__{safe(sender_id)}.json")


def get_session(space_id, sender_id):
    path = _session_path(space_id, sender_id)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def set_session(space_id, sender_id, data):
    path = _session_path(space_id, sender_id)
    with open(path, "w") as f:
        json.dump(data, f)


def clear_session(space_id, sender_id):
    path = _session_path(space_id, sender_id)
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass
