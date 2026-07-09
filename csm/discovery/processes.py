"""Process adapter: finds running Claude-like processes via psutil."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ..config import Config
from ..models import ProcessInfo
from ..redact import redact

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


def _matches(cmdline: str, patterns: list[str]) -> bool:
    return any(re.search(p, cmdline) for p in patterns)


def discover_processes(config: Config) -> list[ProcessInfo]:
    """Return ProcessInfo for every process whose command line matches config
    patterns. Command lines are redacted before storage."""
    if psutil is None:
        return []
    results: list[ProcessInfo] = []
    for proc in psutil.process_iter(["pid", "ppid", "cmdline", "create_time", "memory_info"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if not cmdline or not _matches(cmdline, config.process_match_patterns):
                continue
            # Exclude ourselves and our own server/tests.
            if "claude-session-monitor" in cmdline or re.search(r"(^|/| )csm( |$)", cmdline):
                continue
            try:
                cwd = proc.cwd()
            except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
                cwd = None
            try:
                tty = proc.terminal()
            except (psutil.Error, OSError, AttributeError):
                tty = None
            mem = proc.info.get("memory_info")
            results.append(ProcessInfo(
                pid=proc.info["pid"],
                ppid=proc.info.get("ppid"),
                command=redact(cmdline),
                cwd=cwd,
                tty=tty,
                cpu_percent=proc.cpu_percent(interval=0.0),
                memory_mb=round(mem.rss / (1024 * 1024), 1) if mem else None,
                started_at=datetime.fromtimestamp(proc.info["create_time"], tz=timezone.utc)
                if proc.info.get("create_time") else None,
            ))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return results
