"""Filesystem adapter: discovers Claude Code sessions from transcript files.

Primary source: ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl
Each line is a JSON object; relevant fields observed in real transcripts:
  cwd, gitBranch, sessionId, timestamp, aiTitle, slug, lastPrompt,
  message.model, message.usage.{input_tokens,output_tokens}
Also picks up markdown notes (e.g. ~/.claude/plans/*.md) whose name or content
references a session, and generic *.md/*.log files in configured scan paths.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..config import Config
from ..models import Progress, Session, SubAgent, Todo, UsageInfo

PROJECT_MARKERS = [
    ".git", "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
    "Makefile", "setup.py", "CMakeLists.txt",
]

# Cheap substring pre-filters so we don't json-parse every line of big files.
_INTERESTING = ('"model"', '"usage"', '"cwd"', '"gitBranch"', '"aiTitle"',
                '"slug"', '"timestamp"', '"summary"', '"TodoWrite"')


def find_project_root(start: Optional[str]) -> Optional[str]:
    """Walk upward from *start* looking for a project marker."""
    if not start:
        return None
    p = Path(start)
    if not p.exists():
        return None
    for candidate in [p, *p.parents]:
        for marker in PROJECT_MARKERS:
            if (candidate / marker).exists():
                return str(candidate)
    return None


def _parse_ts(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_transcript(path: Path, config: Config) -> Optional[Session]:
    """Parse one Claude Code .jsonl transcript into a Session."""
    try:
        stat = path.stat()
    except OSError:
        return None
    session = Session(id=path.stem, session_file=str(path), source="files")
    session.add_evidence("file", f"transcript {path}")
    session.updated_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

    if stat.st_size > config.max_file_size_mb * 1024 * 1024:
        session.add_evidence("skipped", f"file larger than {config.max_file_size_mb} MB, content not parsed")
        return session

    input_tokens = 0
    output_tokens = 0
    saw_usage = False
    first_ts: Optional[datetime] = None
    last_ts: Optional[datetime] = None
    latest_todos: Optional[list] = None  # TodoWrite REPLACES the list; keep the last
    try:
        with path.open("r", errors="replace") as fh:
            for line in fh:
                if not any(k in line for k in _INTERESTING):
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("cwd"):
                    if session.launch_cwd is None:
                        session.launch_cwd = obj["cwd"]
                    session.cwd = obj["cwd"]
                if obj.get("gitBranch"):
                    session.git_branch = obj["gitBranch"]
                if obj.get("aiTitle"):
                    session.title = obj["aiTitle"]
                elif obj.get("slug") and not session.title:
                    session.title = str(obj["slug"]).replace("-", " ")
                ts = _parse_ts(obj.get("timestamp"))
                if ts:
                    first_ts = first_ts or ts
                    last_ts = ts
                msg = obj.get("message")
                if isinstance(msg, dict):
                    if msg.get("model") and msg["model"] != session.model:
                        session.model = msg["model"]
                        session.add_evidence("jsonl-field", f"model={msg['model']}")
                    usage = msg.get("usage")
                    if isinstance(usage, dict):
                        saw_usage = True
                        input_tokens += usage.get("input_tokens") or 0
                        input_tokens += usage.get("cache_creation_input_tokens") or 0
                        output_tokens += usage.get("output_tokens") or 0
                    # content is a LIST on assistant lines (may be a str on user lines)
                    content = msg.get("content")
                    if isinstance(content, list):
                        for item in content:
                            if (isinstance(item, dict)
                                    and item.get("type") == "tool_use"
                                    and item.get("name") == "TodoWrite"):
                                todos = (item.get("input") or {}).get("todos")
                                if isinstance(todos, list):
                                    latest_todos = todos
    except OSError:
        return session

    if latest_todos is not None:
        session.todos = [
            Todo(content=str(t.get("content", "")),
                 activeForm=str(t.get("activeForm", "")),
                 status=str(t.get("status", "pending")))
            for t in latest_todos if isinstance(t, dict)
        ]
        counts = {"completed": 0, "in_progress": 0, "pending": 0}
        for t in session.todos:
            if t.status in counts:
                counts[t.status] += 1
        session.progress = Progress(
            completed=counts["completed"],
            in_progress=counts["in_progress"],
            pending=counts["pending"],
            total=len(session.todos),
        )

    session.subagents = _discover_subagents(path)

    session.started_at = first_ts
    if last_ts:
        session.updated_at = last_ts
    if saw_usage:
        session.usage = UsageInfo(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            source=str(path),
            confidence="high",
        )
        session.usage.estimated_cost = _estimate_cost(session.model, session.usage, config)
    session.project_root = find_project_root(session.cwd)
    return session


def _subagent_totals(jsonl_path: Path) -> tuple[int, Optional[str]]:
    """Sum total tokens and pick the last real (non-synthetic) model for a subagent.

    All file IO is guarded; a missing/broken transcript yields (0, None)."""
    total_tokens = 0
    model: Optional[str] = None
    try:
        with jsonl_path.open("r", errors="replace") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(obj, dict):
                    continue
                msg = obj.get("message")
                if not isinstance(msg, dict):
                    continue
                m = msg.get("model")
                if m and m != "<synthetic>":
                    model = m
                usage = msg.get("usage")
                if isinstance(usage, dict):
                    total_tokens += usage.get("input_tokens") or 0
                    total_tokens += usage.get("cache_creation_input_tokens") or 0
                    total_tokens += usage.get("cache_read_input_tokens") or 0
                    total_tokens += usage.get("output_tokens") or 0
    except OSError:
        return 0, None
    return total_tokens, model


def _discover_subagents(transcript: Path) -> list[SubAgent]:
    """Read externalized subagent transcripts beside <sid>.jsonl.

    Layout: <dir>/<sid>/subagents/agent-<id>.meta.json + agent-<id>.jsonl.
    Missing dir -> empty list; malformed json is skipped, never raised."""
    subagents: list[SubAgent] = []
    subdir = transcript.parent / transcript.stem / "subagents"
    try:
        if not subdir.is_dir():
            return subagents
        metas = sorted(subdir.glob("agent-*.meta.json"))
    except OSError:
        return subagents
    for meta_path in metas:
        try:
            meta = json.loads(meta_path.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        # stem is "agent-<id>.meta"; agent id is the filename stem after "agent-"
        agent_id = meta_path.name[len("agent-"):-len(".meta.json")]
        total_tokens, model = 0, None
        jsonl_path = subdir / f"agent-{agent_id}.jsonl"
        if jsonl_path.is_file():
            total_tokens, model = _subagent_totals(jsonl_path)
        subagents.append(SubAgent(
            agent_id=agent_id,
            agent_type=meta.get("agentType"),
            description=meta.get("description"),
            tool_use_id=meta.get("toolUseId"),
            spawn_depth=meta.get("spawnDepth") or 1,
            model=model,
            total_tokens=total_tokens,
        ))
    return subagents


def _estimate_cost(model: Optional[str], usage: UsageInfo, config: Config) -> Optional[float]:
    """USD estimate only if the user configured prices for this model."""
    if not model or model not in config.model_prices:
        return None
    prices = config.model_prices[model]
    try:
        cost = (usage.input_tokens or 0) / 1e6 * float(prices.get("input", 0)) + \
               (usage.output_tokens or 0) / 1e6 * float(prices.get("output", 0))
        return round(cost, 4)
    except (TypeError, ValueError):
        return None


def _first_heading(path: Path, max_bytes: int = 65536) -> Optional[str]:
    try:
        text = path.read_text(errors="replace")[:max_bytes]
    except OSError:
        return None
    for line in text.splitlines():
        m = re.match(r"^#\s+(.+)", line.strip())
        if m:
            return m.group(1).strip()
    return None


def attach_markdown_notes(sessions: list[Session], config: Config) -> None:
    """Link markdown files that reference a session id (in name or first 64KB)."""
    md_files: list[Path] = []
    for base in config.expanded_scan_paths():
        if not base.is_dir() or config.is_ignored(base):
            continue
        md_files.extend(f for f in base.rglob("*.md") if not config.is_ignored(f))
    by_id = {s.id: s for s in sessions}
    for md in md_files:
        stem_ids = [sid for sid in by_id if sid in md.name]
        if stem_ids:
            sess = by_id[stem_ids[0]]
            sess.markdown_file = str(md)
            sess.add_evidence("markdown", f"file name references session id: {md}")
            if not sess.title:
                sess.title = _first_heading(md)


class FilesAdapter:
    name = "files"

    def discover(self, config: Config) -> list[Session]:
        sessions: list[Session] = []
        for base in config.expanded_scan_paths():
            if not base.is_dir() or config.is_ignored(base):
                continue
            for jsonl in base.rglob("*.jsonl"):
                if config.is_ignored(jsonl):
                    continue
                # skip obvious non-transcript jsonl (e.g. history)
                if jsonl.name == "history.jsonl":
                    continue
                # Externalized subagent transcripts (<sid>/subagents/agent-<id>.jsonl,
                # including nested .../subagents/workflows/wf_.../agent-<id>.jsonl)
                # are already surfaced via _discover_subagents() on their parent
                # session. Without this they also get parsed here as bogus
                # independent top-level "sessions" (id "agent-<hash>", grouped by
                # whatever cwd their tool calls happened to run in).
                if "subagents" in jsonl.parts:
                    continue
                s = parse_transcript(jsonl, config)
                if s:
                    sessions.append(s)
        attach_markdown_notes(sessions, config)
        return sessions
