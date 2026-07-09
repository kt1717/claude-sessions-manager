"""csm — terminal interface (argparse + rich)."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.tree import Tree

from . import __version__
from .config import DEFAULT_CONFIG_PATH, load_config, write_default_config
from .discovery import build_snapshot
from .launcher import (LaunchError, build_open_command, build_read_command,
                       open_terminal, pick_terminal, read_file_preview)
from .models import Session, Snapshot
from .redact import redact

console = Console()

STATUS_STYLE = {"active": "bold green", "idle": "yellow", "completed": "dim", "unknown": "red"}


def _fmt_time(dt) -> str:
    if not dt:
        return "unknown"
    local = dt.astimezone()
    return local.strftime("%m-%d %H:%M")


def _fmt_tokens(sess: Session) -> str:
    if sess.usage.total_tokens is None:
        return "unknown"
    return f"{sess.usage.total_tokens:,}"


def _short(path, maxlen=40) -> str:
    if not path:
        return "unknown"
    s = str(path).replace(str(Path.home()), "~")
    return s if len(s) <= maxlen else "…" + s[-(maxlen - 1):]


# ---------------------------------------------------------------- top / list

def _session_table(snapshot: Snapshot, active_only: bool = False) -> Table:
    table = Table(title=f"Claude sessions — {snapshot.generated_at.astimezone():%H:%M:%S}",
                  expand=True)
    for col in ("status", "mission", "project", "pid", "model", "cpu%", "mem MB",
                "tokens", "updated", "session"):
        table.add_column(col, overflow="fold")
    for mission in snapshot.missions:
        for s in mission.sessions:
            if active_only and s.status != "active":
                continue
            p = s.process
            table.add_row(
                f"[{STATUS_STYLE[s.status]}]{s.status}[/]",
                mission.name,
                _short(s.project_root or s.cwd, 30),
                str(p.pid) if p else "-",
                s.model or "unknown",
                f"{p.cpu_percent:.0f}" if p and p.cpu_percent is not None else "-",
                f"{p.memory_mb:.0f}" if p and p.memory_mb is not None else "-",
                _fmt_tokens(s),
                _fmt_time(s.updated_at),
                s.id[:8],
            )
    return table


def cmd_top(args, config) -> int:
    interval = args.interval or config.refresh_interval_seconds
    try:
        with Live(console=console, screen=False, auto_refresh=False) as live:
            while True:
                snapshot = build_snapshot(config)
                live.update(_session_table(snapshot, active_only=not args.all), refresh=True)
                time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def _parse_since(text: str) -> datetime:
    """Accept '2h', '30m', '3d' or ISO dates."""
    now = datetime.now(timezone.utc)
    units = {"m": "minutes", "h": "hours", "d": "days"}
    if text and text[-1] in units and text[:-1].isdigit():
        return now - timedelta(**{units[text[-1]]: int(text[:-1])})
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def cmd_list(args, config) -> int:
    snapshot = build_snapshot(config)
    since = _parse_since(args.since) if args.since else None
    table = Table(expand=True)
    for col in ("id", "status", "mission", "project", "model", "tokens", "updated", "confidence"):
        table.add_column(col, overflow="fold")
    shown = 0
    for mission in snapshot.missions:
        if args.mission and args.mission.lower() not in mission.name.lower():
            continue
        for s in mission.sessions:
            if args.active and s.status != "active":
                continue
            if args.project and args.project not in (s.project_root or "") + (s.cwd or ""):
                continue
            if args.model and args.model not in (s.model or ""):
                continue
            if since and (not s.updated_at or s.updated_at < since):
                continue
            table.add_row(s.id[:8], f"[{STATUS_STYLE[s.status]}]{s.status}[/]", mission.name,
                          _short(s.project_root or s.cwd, 35), s.model or "unknown",
                          _fmt_tokens(s), _fmt_time(s.updated_at), s.confidence)
            shown += 1
    console.print(table)
    console.print(f"{shown} session(s) shown. Use `csm detail <id>` for details.")
    return 0


# ---------------------------------------------------------------------- tree

def cmd_tree(args, config) -> int:
    snapshot = build_snapshot(config)
    root = Tree("[bold]Claude sessions[/]")
    for mission in snapshot.missions:
        mnode = root.add(f"[bold cyan]{mission.name}[/] "
                         f"({len(mission.sessions)} session(s))")
        pnode = mnode.add(f"project: {_short(mission.project_root, 60)}"
                          + (f"  [dim]branch {mission.git_branch}[/]" if mission.git_branch else ""))
        for s in mission.sessions:
            label = (f"[{STATUS_STYLE[s.status]}]{s.status}[/] {s.id[:8]} "
                     f"— {s.title or 'untitled'} [dim]({s.model or 'unknown model'})[/]")
            if s.process:
                tnode = pnode.add(f"terminal pid {s.process.pid}"
                                  + (f" tty {s.process.tty}" if s.process.tty else ""))
                snode = tnode.add(label)
            else:
                snode = pnode.add(label)
            for name, f in (("session", s.session_file), ("markdown", s.markdown_file),
                            ("log", s.log_file)):
                if f:
                    snode.add(f"[dim]{name}: {_short(f, 70)}[/]")
    if snapshot.orphan_processes:
        onode = root.add("[red]uncorrelated Claude processes[/]")
        for p in snapshot.orphan_processes:
            onode.add(f"pid {p.pid} — {redact(p.command)[:80]}")
    console.print(root)
    return 0


# ------------------------------------------------------------------- detail

def _get_session_or_die(session_id: str, config) -> tuple[Snapshot, Session]:
    snapshot = build_snapshot(config)
    sess = snapshot.find_session(session_id)
    if not sess:
        console.print(f"[red]No session matching id '{session_id}'.[/] Try `csm list`.")
        sys.exit(1)
    return snapshot, sess


def cmd_detail(args, config) -> int:
    _, s = _get_session_or_die(args.session_id, config)
    console.print(f"[bold]Session {s.id}[/]")
    rows = [
        ("title", s.title or "unknown"), ("status", s.status),
        ("model", s.model or "unknown"),
        ("tokens in/out", f"{s.usage.input_tokens}/{s.usage.output_tokens}"
         if s.usage.total_tokens is not None else "unknown"),
        ("estimated cost", f"${s.usage.estimated_cost}" if s.usage.estimated_cost is not None
         else "unknown (configure model_prices to enable)"),
        ("project root", s.project_root or "unknown"), ("cwd", s.cwd or "unknown"),
        ("git branch", s.git_branch or "unknown"),
        ("session file", s.session_file or "unknown"),
        ("markdown file", s.markdown_file or "none found"),
        ("log file", s.log_file or "none found"),
        ("started", _fmt_time(s.started_at)), ("updated", _fmt_time(s.updated_at)),
        ("correlation confidence", s.confidence),
    ]
    if s.process:
        p = s.process
        rows += [("pid/ppid", f"{p.pid}/{p.ppid}"), ("tty", p.tty or "unknown"),
                 ("cpu/mem", f"{p.cpu_percent}% / {p.memory_mb} MB"),
                 ("command", redact(p.command))]
    table = Table(show_header=False)
    table.add_column(style="cyan"); table.add_column(overflow="fold")
    for k, v in rows:
        table.add_row(k, str(v))
    console.print(table)
    if s.evidence:
        console.print("[bold]Evidence:[/]")
        for e in s.evidence:
            console.print(f"  • [dim]{e.kind}[/]: {redact(e.detail)}")
    console.print("\n[bold]Launch commands:[/]")
    target = s.project_root or s.cwd
    try:
        if target:
            console.print(f"  open terminal : {' '.join(build_open_command(target, config))}")
    except LaunchError as exc:
        console.print(f"  open terminal : unavailable ({exc})")
    for f in (s.markdown_file, s.session_file):
        if f:
            try:
                console.print(f"  read file     : {' '.join(build_read_command(f, config))}")
            except LaunchError as exc:
                console.print(f"  read file     : unavailable ({exc})")
            break
    return 0


# ---------------------------------------------------------------- open/read

def cmd_open(args, config) -> int:
    _, s = _get_session_or_die(args.session_id, config)
    target = s.project_root or s.cwd
    if not target or not Path(target).is_dir():
        console.print(f"[red]No existing project directory for session {s.id[:8]}.[/]")
        return 1
    try:
        argv = build_open_command(target, config)
    except LaunchError as exc:
        console.print(f"[red]{exc}[/]")
        return 1
    console.print(f"Command: [bold]{' '.join(argv)}[/]  (cwd: {target})")
    if not args.yes and config.require_confirmation_for_open:
        answer = console.input("Run it? \\[y/N] ").strip().lower()
        if answer != "y":
            console.print("Not launched.")
            return 0
    open_terminal(target, config)
    console.print("[green]Terminal launched.[/]")
    if args.read and (s.markdown_file or s.session_file):
        return cmd_read(args, config)
    return 0


def cmd_read(args, config) -> int:
    _, s = _get_session_or_die(args.session_id, config)
    target = s.markdown_file or s.log_file or s.session_file
    if not target:
        console.print("[red]No readable file recorded for this session.[/]")
        return 1
    try:
        argv = build_read_command(target, config)
        if sys.stdout.isatty() and not getattr(args, "preview", False):
            return subprocess.call(argv)
    except LaunchError:
        pass
    console.print(redact(read_file_preview(target)))
    return 0


# -------------------------------------------------------- doctor/config/etc

def cmd_doctor(args, config) -> int:
    ok = True

    def check(label, good, hint=""):
        nonlocal ok
        mark = "[green]OK[/]" if good else "[red]FAIL[/]"
        console.print(f"  {mark}  {label}" + (f"  [dim]{hint}[/]" if hint and not good else ""))
        ok = ok and good

    console.print("[bold]csm doctor[/]")
    check(f"config file {DEFAULT_CONFIG_PATH}", DEFAULT_CONFIG_PATH.is_file(),
          "run `csm config init`")
    for p in config.expanded_scan_paths():
        check(f"scan path {p}", p.is_dir(), "path missing — edit scan_paths in config")
    try:
        import psutil  # noqa: F401
        check("psutil available", True)
    except ImportError:
        check("psutil available", False, "pip install psutil — process detection disabled")
    term = None
    try:
        term = pick_terminal(config)
    except LaunchError as exc:
        check(f"terminal launcher ({exc})", False)
    check(f"terminal launcher: {term or 'none found'}", term is not None,
          "install gnome-terminal/konsole/xterm or set terminal_launcher")
    import shutil as _sh
    check(f"safe reader '{config.safe_read_command}'", bool(_sh.which(config.safe_read_command)),
          "set safe_read_command to an available pager")
    snapshot = build_snapshot(config, include_processes=True)
    check(f"discovery: {len(snapshot.sessions)} session(s), "
          f"{len(snapshot.orphan_processes)} uncorrelated process(es)", True)
    console.print("[green]All checks passed.[/]" if ok else "[yellow]Some checks failed.[/]")
    return 0 if ok else 1


def cmd_config_init(args, config) -> int:
    try:
        path = write_default_config(force=args.force)
    except FileExistsError as exc:
        console.print(f"[yellow]{exc}[/]")
        return 1
    console.print(f"[green]Wrote {path}[/]")
    return 0


def cmd_export(args, config) -> int:
    snapshot = build_snapshot(config)
    payload = snapshot.model_dump(mode="json")
    text = json.dumps(payload, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(text)
        console.print(f"Exported to {args.output}")
    else:
        print(text)
    return 0


GUIDE = """\
csm — Claude session monitor. Quick guide:
  csm doctor                check environment and detection
  csm config init           write default config (~/.config/claude-session-monitor/)
  csm list [--active]       list detected sessions
  csm tree                  mission -> project -> process -> session -> files
  csm top [-n SECS]         live view of active sessions (Ctrl-C to exit)
  csm detail <id>           everything known about one session (id prefix ok)
  csm read <id>             page through session markdown/log (read-only)
  csm open <id> [--yes]     open a terminal at the project folder (confirmed)
  csm export [-o FILE]      dump inventory as JSON
  csm serve                 local web dashboard at http://127.0.0.1:8765
Full docs: docs/USER_GUIDE.md in the repository.
"""


def cmd_guide(args, config) -> int:
    console.print(GUIDE)
    return 0


def cmd_serve(args, config) -> int:
    from .server import run_server
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        console.print("[bold red]WARNING:[/] binding beyond localhost exposes session "
                      "metadata (paths, commands) to your network. Actions stay disabled "
                      "unless enable_gui_actions is true.")
    run_server(host=args.host, port=args.port, config=config)
    return 0


# ------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="csm",
                                     description="Monitor Claude/Claude Code sessions on this machine.")
    parser.add_argument("--version", action="version", version=f"csm {__version__}")
    parser.add_argument("--config", help="alternate config file path")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("top", help="live top-like view")
    p.add_argument("-n", "--interval", type=float, help="refresh seconds")
    p.add_argument("--all", action="store_true", help="include non-active sessions")
    p.set_defaults(func=cmd_top)

    p = sub.add_parser("tree", help="mission/project/session tree")
    p.set_defaults(func=cmd_tree)

    p = sub.add_parser("list", help="list sessions")
    p.add_argument("--active", action="store_true")
    p.add_argument("--mission")
    p.add_argument("--project")
    p.add_argument("--model")
    p.add_argument("--since", help="e.g. 2h, 30m, 3d, or ISO date")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("detail", help="show one session")
    p.add_argument("session_id")
    p.set_defaults(func=cmd_detail)

    p = sub.add_parser("open", help="open terminal at the session's project folder")
    p.add_argument("session_id")
    p.add_argument("--yes", action="store_true", help="skip confirmation")
    p.add_argument("--read", action="store_true", help="also read the session file")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("read", help="read session markdown/log safely")
    p.add_argument("session_id")
    p.add_argument("--preview", action="store_true", help="print instead of paging")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("doctor", help="diagnose configuration and detection")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("config", help="configuration commands")
    csub = p.add_subparsers(dest="config_command", required=True)
    ci = csub.add_parser("init", help="write default config file")
    ci.add_argument("--force", action="store_true")
    ci.set_defaults(func=cmd_config_init)

    p = sub.add_parser("export", help="export inventory to JSON")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("guide", help="print a short usage guide")
    p.set_defaults(func=cmd_guide)

    p = sub.add_parser("serve", help="run the local web dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    return args.func(args, config)


if __name__ == "__main__":
    sys.exit(main())
