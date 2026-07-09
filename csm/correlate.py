"""Correlate running processes with discovered session files.

Confidence rules (documented, deterministic):
  high   — process cwd == session cwd (exact path match)
  medium — process cwd inside session project_root (or vice versa)
  low    — process matched only by recency (session updated after process start)
  unknown— no process attached
Each match records evidence so users can debug correlation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config
from .models import ProcessInfo, Session


def _is_subpath(child: str, parent: str) -> bool:
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except (ValueError, OSError):
        return False


def correlate(sessions: list[Session], processes: list[ProcessInfo],
              config: Config) -> list[ProcessInfo]:
    """Attach processes to sessions in place; return unmatched processes."""
    unmatched = list(processes)

    def take(proc: ProcessInfo) -> None:
        if proc in unmatched:
            unmatched.remove(proc)

    # Pass 1: exact cwd match (high)
    for sess in sessions:
        if sess.process or not sess.cwd:
            continue
        for proc in unmatched:
            if proc.cwd and proc.cwd == sess.cwd:
                sess.process = proc
                sess.confidence = "high"
                sess.add_evidence("cwd-match", f"pid {proc.pid} cwd == session cwd {sess.cwd}")
                take(proc)
                break

    # Pass 2: same project root (medium)
    for sess in sessions:
        if sess.process:
            continue
        root = sess.project_root or sess.cwd
        if not root:
            continue
        for proc in unmatched:
            if proc.cwd and (_is_subpath(proc.cwd, root) or _is_subpath(root, proc.cwd)):
                sess.process = proc
                sess.confidence = "medium"
                sess.add_evidence("project-root-match",
                                  f"pid {proc.pid} cwd {proc.cwd} within/above {root}")
                take(proc)
                break

    # Pass 3: recency only (low) — session updated after the process started
    for sess in sessions:
        if sess.process or not sess.updated_at:
            continue
        for proc in unmatched:
            if proc.started_at and sess.updated_at >= proc.started_at:
                sess.process = proc
                sess.confidence = "low"
                sess.add_evidence("mtime", f"session updated after pid {proc.pid} started")
                take(proc)
                break

    _assign_status(sessions, config)
    return unmatched


def _assign_status(sessions: list[Session], config: Config) -> None:
    now = datetime.now(timezone.utc)
    idle_cutoff = now - timedelta(minutes=config.idle_after_minutes)
    completed_cutoff = now - timedelta(hours=config.completed_after_hours)
    for sess in sessions:
        if sess.source == "mock" and sess.status != "unknown":
            continue  # mock data may pre-set status
        if sess.process:
            sess.status = "active"
        elif sess.updated_at is None:
            sess.status = "unknown"
        elif sess.updated_at >= idle_cutoff:
            sess.status = "idle"
        elif sess.updated_at < completed_cutoff:
            sess.status = "completed"
        else:
            sess.status = "idle"
