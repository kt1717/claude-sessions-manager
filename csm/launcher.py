"""Safe launcher: open a terminal at a project path, or read a session file.

Safety design:
- Commands are built as argv lists — never through a shell.
- Only whitelisted terminal programs can be launched.
- Target paths must exist and be directories/files we discovered.
- Nothing extracted from session file CONTENT is ever executed.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .config import Config

# launcher name -> argv template; {dir} is replaced with the validated directory.
TERMINAL_WHITELIST: dict[str, list[str]] = {
    "gnome-terminal": ["gnome-terminal", "--working-directory={dir}"],
    "konsole": ["konsole", "--workdir", "{dir}"],
    # xterm / x-terminal-emulator have no workdir flag; they inherit cwd from Popen.
    "xterm": ["xterm"],
    "x-terminal-emulator": ["x-terminal-emulator"],
    "tmux": ["tmux", "new-window", "-c", "{dir}"],
}

# launcher name -> argv template for `claude --resume <id>`; {dir}/{id} substituted.
RESUME_WHITELIST: dict[str, list[str]] = {
    "gnome-terminal": ["gnome-terminal", "--working-directory={dir}",
                       "--", "claude", "--resume", "{id}"],
    "konsole": ["konsole", "--workdir", "{dir}", "-e", "claude", "--resume", "{id}"],
    "xterm": ["xterm", "-e", "claude", "--resume", "{id}"],
    "x-terminal-emulator": ["x-terminal-emulator", "-e", "claude", "--resume", "{id}"],
    "tmux": ["tmux", "new-window", "-c", "{dir}", "claude", "--resume", "{id}"],
}

# A session id must look like a uuid or a safe token before it reaches an argv.
_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

SAFE_READERS = {"less", "more", "cat", "bat", "head"}


class LaunchError(Exception):
    pass


def pick_terminal(config: Config) -> Optional[str]:
    if config.terminal_launcher != "auto":
        name = config.terminal_launcher
        if name not in TERMINAL_WHITELIST:
            raise LaunchError(
                f"terminal_launcher '{name}' is not in the whitelist: "
                f"{sorted(TERMINAL_WHITELIST)}")
        return name if shutil.which(name) else None
    for name in ("gnome-terminal", "konsole", "x-terminal-emulator", "xterm", "tmux"):
        if shutil.which(name):
            return name
    return None


def build_open_command(directory: str, config: Config) -> list[str]:
    """Return the argv to open a terminal at *directory* (validated)."""
    d = Path(directory).expanduser()
    if not d.is_dir():
        raise LaunchError(f"not an existing directory: {directory}")
    term = pick_terminal(config)
    if not term:
        raise LaunchError("no whitelisted terminal emulator found (see `csm doctor`)")
    return [part.format(dir=str(d.resolve())) for part in TERMINAL_WHITELIST[term]]


def open_terminal(directory: str, config: Config, dry_run: bool = False) -> list[str]:
    argv = build_open_command(directory, config)
    if not dry_run:
        subprocess.Popen(argv, start_new_session=True, cwd=str(Path(directory).expanduser()),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return argv


def build_resume_command(session_id: str, directory: str, config: Config) -> list[str]:
    """Return argv to open a terminal running `claude --resume <id>` at *directory*.

    Both inputs are validated before they reach the argv; no shell is used."""
    if not _SAFE_SESSION_ID.match(session_id or ""):
        raise LaunchError(f"unsafe session id: {session_id!r}")
    d = Path(directory).expanduser()
    if not d.is_dir():
        raise LaunchError(f"not an existing directory: {directory}")
    term = pick_terminal(config)
    if not term:
        raise LaunchError("no whitelisted terminal emulator found (see `csm doctor`)")
    return [part.format(dir=str(d.resolve()), id=session_id)
            for part in RESUME_WHITELIST[term]]


def resume_session(session_id: str, directory: str, config: Config,
                   dry_run: bool = False) -> list[str]:
    argv = build_resume_command(session_id, directory, config)
    if not dry_run:
        subprocess.Popen(argv, start_new_session=True,
                         cwd=str(Path(directory).expanduser()),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return argv


def build_read_command(file_path: str, config: Config) -> list[str]:
    """Return argv to read *file_path* with the configured safe pager."""
    f = Path(file_path).expanduser()
    if not f.is_file():
        raise LaunchError(f"not an existing file: {file_path}")
    reader = config.safe_read_command or "less"
    base = Path(reader).name
    if base not in SAFE_READERS:
        raise LaunchError(f"safe_read_command '{reader}' not in whitelist {sorted(SAFE_READERS)}")
    if not shutil.which(reader):
        raise LaunchError(f"reader '{reader}' not found on PATH")
    return [reader, str(f.resolve())]


def read_file_preview(file_path: str, max_bytes: int = 100_000) -> str:
    """Safe in-process preview (used by the GUI and as `csm read` fallback)."""
    f = Path(file_path).expanduser()
    if not f.is_file():
        raise LaunchError(f"not an existing file: {file_path}")
    data = f.read_bytes()[:max_bytes]
    text = data.decode("utf-8", errors="replace")
    if f.stat().st_size > max_bytes:
        text += f"\n... [truncated at {max_bytes} bytes of {f.stat().st_size}]"
    return text
