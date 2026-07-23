"""Persistence for user-assigned (renamable) task names and archived sessions.

Sidecar JSON, shape:
    {"sessions": {"<session-id>": "name"}, "missions": {"<project_root>": "name"},
     "archived": ["<session-id>", ...]}

Archiving only ever hides a session from the default dashboard view via this
sidecar file — it never touches the underlying transcript/markdown/log files.

All IO is defensive: a missing or malformed file reads as empty, and a write
never leaves a half-written file (write-to-temp then atomic replace).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .config import Config

MAX_NAME_LEN = 200


class LabelError(ValueError):
    """Raised for an invalid rename name (empty / too long)."""


def sanitize_name(name: str) -> str:
    """Normalize a user-supplied task name or raise LabelError.

    Strips control characters (newlines/tabs), collapses surrounding
    whitespace, rejects empty and over-long names.
    """
    if not isinstance(name, str):
        raise LabelError("name must be a string")
    cleaned = "".join(ch for ch in name if ch == " " or ord(ch) >= 0x20).strip()
    if not cleaned:
        raise LabelError("name must not be empty")
    if len(cleaned) > MAX_NAME_LEN:
        raise LabelError(f"name too long (max {MAX_NAME_LEN} characters)")
    return cleaned


def _empty() -> dict:
    return {"sessions": {}, "missions": {}, "archived": []}


def load_labels(config: Config) -> dict:
    """Return the labels mapping; empty structure if absent/broken."""
    path = config.labels_path()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    sessions = data.get("sessions")
    missions = data.get("missions")
    archived = data.get("archived")
    return {
        "sessions": sessions if isinstance(sessions, dict) else {},
        "missions": missions if isinstance(missions, dict) else {},
        "archived": [a for a in archived if isinstance(a, str)] if isinstance(archived, list) else [],
    }


def save_labels(config: Config, data: dict) -> None:
    """Atomically write the labels mapping."""
    path = config.labels_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.replace(tmp, path)


def set_session_label(config: Config, session_id: str, name: str) -> str:
    """Persist a session rename; returns the sanitized name."""
    clean = sanitize_name(name)
    data = load_labels(config)
    data["sessions"][session_id] = clean
    save_labels(config, data)
    return clean


def set_mission_label(config: Config, project_root: str, name: str) -> str:
    """Persist a mission rename keyed by project_root; returns sanitized name."""
    clean = sanitize_name(name)
    data = load_labels(config)
    data["missions"][project_root] = clean
    save_labels(config, data)
    return clean


def set_archived(config: Config, session_id: str, archived: bool) -> bool:
    """Hide/unhide a session from the default dashboard view. Never touches
    the underlying transcript/markdown/log files — this is display-only."""
    data = load_labels(config)
    ids = set(data["archived"])
    if archived:
        ids.add(session_id)
    else:
        ids.discard(session_id)
    data["archived"] = sorted(ids)
    save_labels(config, data)
    return archived
