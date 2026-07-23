"""Configuration loading for claude-session-monitor (YAML)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

DEFAULT_CONFIG_PATH = Path(
    os.environ.get("CSM_CONFIG", "~/.config/claude-session-monitor/config.yaml")
).expanduser()

DEFAULT_SCAN_PATHS = [
    "~/.claude/projects",   # Claude Code transcripts (*.jsonl)
    "~/.claude/plans",      # plan markdown files
]

DEFAULT_PROCESS_PATTERNS = [
    r"(^|/| )claude( |$)",
    r"claude-code",
    r"@anthropic-ai/claude",
]

DEFAULT_MODEL_PATTERNS = [
    r'"model"\s*:\s*"(claude-[a-z0-9.\-]+)"',
    r"\b(claude-(?:fable|mythos|opus|sonnet|haiku)-[a-z0-9.\-]+)\b",
]


@dataclass
class Config:
    scan_paths: list[str] = field(default_factory=lambda: list(DEFAULT_SCAN_PATHS))
    ignored_paths: list[str] = field(default_factory=list)
    # auto | gnome-terminal | konsole | xterm | tmux | wt | powershell | cmd | wt-wsl
    terminal_launcher: str = "auto"
    editor_command: str = ""          # e.g. "code" — empty means unset
    safe_read_command: str = "less"
    refresh_interval_seconds: float = 3.0
    process_match_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_PROCESS_PATTERNS))
    mission_detection_rules: list[str] = field(
        default_factory=lambda: ["folder_name", "git_branch", "markdown_title"]
    )
    model_detection_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_MODEL_PATTERNS))
    usage_detection_patterns: list[str] = field(default_factory=list)  # reserved for custom adapters
    max_file_size_mb: float = 50.0
    enable_gui_actions: bool = True
    require_confirmation_for_open: bool = True
    idle_after_minutes: float = 60.0       # newer than this without a process -> idle
    completed_after_hours: float = 24.0    # older than this -> completed
    mock_data_file: str = ""               # if set, mock adapter loads this JSON
    # Optional per-model USD price per 1M tokens: {"claude-sonnet-5": {"input": 3, "output": 15}}
    model_prices: dict = field(default_factory=dict)
    # Sidecar file holding user-assigned session/mission names. Empty -> default location.
    labels_file: str = ""

    def expanded_scan_paths(self) -> list[Path]:
        return [Path(p).expanduser() for p in self.scan_paths]

    def labels_path(self) -> Path:
        """Where renamable-task overrides live (next to the config file)."""
        if self.labels_file:
            return Path(self.labels_file).expanduser()
        return DEFAULT_CONFIG_PATH.parent / "labels.json"

    def is_ignored(self, path: Path) -> bool:
        s = str(path)
        return any(s.startswith(str(Path(p).expanduser())) for p in self.ignored_paths)


def load_config(path: Optional[Path] = None) -> Config:
    """Load config from YAML; unknown keys are ignored, missing keys use defaults."""
    path = Path(path).expanduser() if path else DEFAULT_CONFIG_PATH
    cfg = Config()
    if path.is_file():
        data = yaml.safe_load(path.read_text()) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Config file {path} must contain a YAML mapping")
        for key, value in data.items():
            if hasattr(cfg, key) and value is not None:
                setattr(cfg, key, value)
    return cfg


DEFAULT_CONFIG_YAML = """\
# claude-session-monitor configuration
# Paths scanned for Claude Code transcripts (*.jsonl), markdown notes, logs.
scan_paths:
  - ~/.claude/projects
  - ~/.claude/plans

# Paths never scanned or displayed.
ignored_paths: []

# Terminal used by `csm open`: auto | gnome-terminal | konsole | xterm | tmux |
# wt (Windows Terminal, native Windows) | powershell (native Windows fallback) |
# cmd (native Windows, last-resort fallback) |
# wt-wsl (bounces from inside WSL out to a Windows Terminal window)
terminal_launcher: auto

# Editor for opening files (used only if set), e.g. "code" or "vim".
editor_command: ""

# Read-only pager for `csm read`.
safe_read_command: less

refresh_interval_seconds: 3.0

# Regexes matched against process command lines to detect Claude sessions.
process_match_patterns:
  - "(^|/| )claude( |$)"
  - "claude-code"
  - "@anthropic-ai/claude"

# Order of strategies for naming a mission.
mission_detection_rules: [folder_name, git_branch, markdown_title]

model_detection_patterns:
  - '"model"\\s*:\\s*"(claude-[a-z0-9.\\-]+)"'

usage_detection_patterns: []

# Files larger than this are skipped for content parsing (still listed).
max_file_size_mb: 50.0

enable_gui_actions: true
require_confirmation_for_open: true

# Session freshness thresholds.
idle_after_minutes: 60.0
completed_after_hours: 24.0

# Optional: USD per 1M tokens, per model. Leave empty to show cost as unknown.
model_prices: {}
"""


def write_default_config(path: Optional[Path] = None, force: bool = False) -> Path:
    path = Path(path).expanduser() if path else DEFAULT_CONFIG_PATH
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists (use --force to overwrite)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG_YAML)
    return path
