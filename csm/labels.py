"""Persistence for user-assigned (renamable) task names.

Sidecar JSON, shape:
    {"sessions": {"<session-id>": "name"}, "missions": {"<project_root>": "name"}}

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
    return {"sessions": {}, "missions": {}}


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
    return {
        "sessions": sessions if isinstance(sessions, dict) else {},
        "missions": missions if isinstance(missions, dict) else {},
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
