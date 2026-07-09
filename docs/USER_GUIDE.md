# claude-session-monitor — User Guide

A local "top / Task Manager" for Claude Code sessions: see which sessions are running,
which terminal owns them, what mission/project they belong to, which model they used,
and token usage — all from local files and processes. Nothing leaves your machine.

## 1. Installation

```bash
cd claude-session-monitor
pip3 install --user -e .          # core + CLI
# web GUI extras (FastAPI/uvicorn) if not already present:
pip3 install --user fastapi uvicorn
export PATH="$HOME/.local/bin:$PATH"   # if csm is not found
```

Requires Python ≥ 3.10.

## 2. Quick start

```bash
csm config init     # write default config
csm doctor          # verify everything is detected
csm list            # all sessions   (csm list --active for live ones)
csm tree            # mission → project → process → session → files
csm top             # live htop-style view (Ctrl-C to quit)
csm serve           # web dashboard at http://127.0.0.1:8765
```

## 3. Configuration

`csm config init` creates `~/.config/claude-session-monitor/config.yaml`
(override location with the `CSM_CONFIG` env var or `csm --config PATH …`).
Key settings:

| key | meaning |
|---|---|
| `scan_paths` | directories scanned for transcripts (`*.jsonl`) and markdown notes |
| `ignored_paths` | never scanned or shown |
| `terminal_launcher` | `auto` or one of: gnome-terminal, konsole, xterm, x-terminal-emulator, tmux |
| `safe_read_command` | pager for `csm read` (whitelist: less, more, cat, bat, head) |
| `process_match_patterns` | regexes that identify Claude processes |
| `idle_after_minutes` / `completed_after_hours` | status thresholds |
| `model_prices` | optional USD/1M-token prices to enable cost estimates |
| `require_confirmation_for_open` | ask before launching a terminal (default true) |
| `enable_gui_actions` | allow the web GUI to launch terminals (default true) |

## 4. Terminal monitor

- `csm top` — refreshes every `refresh_interval_seconds` (or `-n 5`); `--all` includes
  idle/completed sessions.
- `csm list` — filters: `--active`, `--mission NAME`, `--project PATH-SUBSTRING`,
  `--model SUBSTRING`, `--since 2h|30m|3d|2026-07-01`.
- `csm detail <id>` — accepts a unique id prefix (the 8-char id from list/tree).

## 5. Web dashboard

```bash
csm serve --host 127.0.0.1 --port 8765
```

Summary cards, a clickable mission→session tree, a detail panel, safe action buttons
(open terminal, read file, copy path/diagnostics), auto-refresh with pause/resume.
The server binds localhost only by default; binding anything else prints a warning —
your session metadata (paths, commands) would be visible to the network.

## 6. How session detection works

1. **Files**: every `*.jsonl` under `scan_paths` (Claude Code writes transcripts to
   `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`) is parsed for cwd, git
   branch, title, timestamps, model, and token usage. Markdown files whose name
   contains a session id are linked as that session's notes.
2. **Processes**: psutil scans command lines against `process_match_patterns`.
3. **Correlation**: a process is attached to a session by exact cwd match (**high**
   confidence), shared project root (**medium**), or start-time recency (**low**).
   The evidence list in `csm detail` shows exactly why a match was made.
4. **Status**: `active` = live process attached; `idle` = file activity within
   `idle_after_minutes`…`completed_after_hours`; `completed` = older; `unknown` = no data.

## 7. Model / usage detection

Model and token counts come only from fields actually present in transcripts
(`message.model`, `message.usage`). Missing data is shown as **unknown — never
invented**. Cost appears only if you configure `model_prices`.

## 8. Customizing scan paths

Add any directory that contains transcripts, project notes, or logs:

```yaml
scan_paths:
  - ~/.claude/projects
  - ~/.claude/plans
  - ~/work/ai-notes
```

Large files are skipped for content parsing above `max_file_size_mb` (still listed).

## 9. Using `csm open`

`csm open <id>` shows the exact command (e.g. `gnome-terminal
--working-directory=/path`) and asks for confirmation; `--yes` skips the prompt;
`--read` also opens the session file afterward. Only whitelisted terminal programs can
run, always as an argv list (no shell), only at a directory that actually exists.

## 10. Troubleshooting

- `csm: command not found` → `export PATH="$HOME/.local/bin:$PATH"`.
- `csm doctor` fails a scan path → the directory doesn't exist; fix `scan_paths`.
- No processes detected → check `process_match_patterns`; some processes need the same
  user; psutil may lack permission to read other users' cwd.
- No sessions found → confirm `~/.claude/projects` exists and holds `*.jsonl`.
- GUI actions return 403 → `enable_gui_actions: false` in config.

## 11. Security notes

- All displayed/served text passes secret redaction (API keys, bearer tokens,
  passwords, private keys, sshpass arguments).
- Session file **content is never executed** — only whitelisted viewers/terminals run.
- Web read endpoints accept only session ids; file paths always come from discovery,
  so path traversal via the API is not possible.
- No network calls, no cloud APIs, no API keys needed.

## 12. Limitations

- Token counts sum `input + cache_creation + output` from the transcript; cache reads
  are excluded, so numbers differ from Anthropic billing. Cost is a rough estimate and
  off by cache pricing even when configured.
- Session↔process correlation is heuristic; check the confidence and evidence.
- Only Claude Code's local transcript format is parsed out of the box (other tools can
  be added as adapters — see the developer guide).
- Status "completed" means "no recent activity", not a confirmed clean exit.
