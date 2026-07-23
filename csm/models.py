"""Core data model for claude-session-monitor.

All fields that cannot be detected are None / "unknown" — never invented.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low", "unknown"]
Status = Literal["active", "idle", "completed", "unknown"]


class ProcessInfo(BaseModel):
    pid: int
    ppid: Optional[int] = None
    command: str = ""
    cwd: Optional[str] = None
    tty: Optional[str] = None
    cpu_percent: Optional[float] = None
    memory_mb: Optional[float] = None
    started_at: Optional[datetime] = None


class UsageInfo(BaseModel):
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    estimated_cost: Optional[float] = None
    source: Optional[str] = None
    confidence: Confidence = "unknown"


class Evidence(BaseModel):
    kind: str  # e.g. "cwd-match", "jsonl-field", "mtime"
    detail: str


class Todo(BaseModel):
    content: str
    activeForm: str = ""
    status: str = "pending"  # pending | in_progress | completed


class Progress(BaseModel):
    completed: int = 0
    in_progress: int = 0
    pending: int = 0
    total: int = 0


class SubAgent(BaseModel):
    agent_id: str
    agent_type: Optional[str] = None
    description: Optional[str] = None
    tool_use_id: Optional[str] = None
    spawn_depth: int = 1
    model: Optional[str] = None
    total_tokens: int = 0
    status: Optional[str] = None


class Session(BaseModel):
    id: str
    title: Optional[str] = None
    status: Status = "unknown"
    model: Optional[str] = None
    usage: UsageInfo = Field(default_factory=UsageInfo)
    process: Optional[ProcessInfo] = None
    project_root: Optional[str] = None
    cwd: Optional[str] = None
    # First cwd seen in the transcript: the directory `claude` was launched from,
    # which is what its ~/.claude/projects/<encoded> folder is keyed on. `cwd` can
    # drift later in the transcript (e.g. the user `cd`s elsewhere), so only
    # launch_cwd is safe to resume `--resume` in.
    launch_cwd: Optional[str] = None
    session_file: Optional[str] = None
    markdown_file: Optional[str] = None
    log_file: Optional[str] = None
    started_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    confidence: Confidence = "unknown"
    evidence: list[Evidence] = Field(default_factory=list)
    git_branch: Optional[str] = None
    source: str = "unknown"  # adapter that produced it
    todos: list[Todo] = Field(default_factory=list)
    progress: Optional[Progress] = None
    subagents: list[SubAgent] = Field(default_factory=list)
    label: Optional[str] = None  # user override; None if unset
    archived: bool = False  # hidden from the default dashboard view; file untouched

    def add_evidence(self, kind: str, detail: str) -> None:
        self.evidence.append(Evidence(kind=kind, detail=detail))


class Mission(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    project_root: Optional[str] = None
    git_branch: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    sessions: list[Session] = Field(default_factory=list)


class Snapshot(BaseModel):
    """One full discovery result."""
    generated_at: datetime
    missions: list[Mission] = Field(default_factory=list)
    orphan_processes: list[ProcessInfo] = Field(default_factory=list)

    @property
    def sessions(self) -> list[Session]:
        return [s for m in self.missions for s in m.sessions]

    def find_session(self, session_id: str) -> Optional[Session]:
        for s in self.sessions:
            if s.id == session_id or s.id.startswith(session_id):
                return s
        return None
