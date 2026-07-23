"""Discovery pipeline: run adapters -> correlate processes -> group into missions."""
from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..config import Config
from ..correlate import correlate
from ..labels import load_labels
from ..models import Mission, Session, Snapshot
from .files import FilesAdapter
from .mock import MockAdapter
from .processes import discover_processes

# Adapter registry — add new adapters here (see developer guide).
ADAPTERS = [FilesAdapter(), MockAdapter()]


def _git_branch(root: str) -> Optional[str]:
    head = Path(root) / ".git" / "HEAD"
    try:
        text = head.read_text().strip()
    except OSError:
        return None
    if text.startswith("ref: refs/heads/"):
        return text.split("refs/heads/", 1)[1]
    return text[:12] if text else None


def _mission_name(root: Optional[str], sessions: list[Session], config: Config) -> str:
    for rule in config.mission_detection_rules:
        if rule == "folder_name" and root:
            return Path(root).name
        if rule == "git_branch" and root:
            branch = _git_branch(root)
            if branch and branch not in ("HEAD", "main", "master"):
                return branch
        if rule == "markdown_title":
            for s in sessions:
                if s.title:
                    return s.title
    return "unknown"


def build_snapshot(config: Config, include_processes: bool = True) -> Snapshot:
    """Full discovery: adapters -> dedupe -> correlate -> missions."""
    sessions: list[Session] = []
    seen_ids: set[str] = set()
    for adapter in ADAPTERS:
        try:
            found = adapter.discover(config)
        except Exception as exc:  # an adapter failure must not kill discovery
            found = []
            print(f"warning: adapter {adapter.name} failed: {exc}")
        for s in found:
            if s.id not in seen_ids:
                seen_ids.add(s.id)
                sessions.append(s)

    # User overrides for renamable tasks (session/mission names) and archiving.
    labels = load_labels(config)
    session_labels = labels.get("sessions", {})
    mission_labels = labels.get("missions", {})
    archived_ids = set(labels.get("archived", []))
    for s in sessions:
        override = session_labels.get(s.id)
        if override:
            s.label = override
            s.title = override  # user name wins over the auto-derived title
        s.archived = s.id in archived_ids

    processes = discover_processes(config) if include_processes else []
    orphans = correlate(sessions, processes, config)

    # Group sessions into missions by project_root (fallback: cwd, then "unknown").
    groups: dict[str, list[Session]] = {}
    for s in sessions:
        key = s.project_root or s.cwd or "unknown"
        groups.setdefault(key, []).append(s)

    missions: list[Mission] = []
    for root, group in sorted(groups.items()):
        group.sort(key=lambda s: (s.updated_at or datetime.min.replace(tzinfo=timezone.utc)),
                   reverse=True)
        real_root = None if root == "unknown" else root
        name = _mission_name(real_root, group, config)
        if real_root and mission_labels.get(real_root):
            name = mission_labels[real_root]  # user name overrides auto-derived
        updated = max((s.updated_at for s in group if s.updated_at), default=None)
        created = min((s.started_at for s in group if s.started_at), default=None)
        missions.append(Mission(
            id=hashlib.sha1(root.encode()).hexdigest()[:12],
            name=name,
            project_root=real_root,
            git_branch=_git_branch(real_root) if real_root else None,
            created_at=created,
            updated_at=updated,
            sessions=group,
        ))
    missions.sort(key=lambda m: (m.updated_at or datetime.min.replace(tzinfo=timezone.utc)),
                  reverse=True)
    return Snapshot(generated_at=datetime.now(timezone.utc), missions=missions,
                    orphan_processes=orphans)
