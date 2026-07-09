# claude-session-monitor — Developer Guide

## 1. Architecture overview

```
csm/
  models.py        Pydantic models: Session, Mission, ProcessInfo, UsageInfo, Snapshot
  config.py        YAML config (dataclass), defaults, config init
  redact.py        secret redaction applied before ANY display/serving
  correlate.py     process↔session matching + status assignment
  launcher.py      safe terminal/pager launching (argv whitelists, no shell)
  discovery/
    __init__.py    pipeline: run ADAPTERS -> dedupe -> correlate -> group missions
    base.py        SessionAdapter protocol
    files.py       Claude Code *.jsonl transcripts + markdown notes
    processes.py   psutil process scan
    mock.py        JSON mock data (dev/tests)
  cli.py           argparse + rich commands (csm …)
  server.py        FastAPI app (csm serve)
  web/index.html   single-file dashboard (vanilla JS, no build step)
```

Both frontends call one function: `csm.discovery.build_snapshot(config)` → `Snapshot`.
Discovery is stateless; every call re-scans (262 sessions ≈ fast, jsonl lines are
pre-filtered by substring before JSON parsing).

## 2. Discovery pipeline

1. Each adapter in `discovery.ADAPTERS` returns `list[Session]`; failures are caught
   and reported as warnings, never fatal. Duplicate session ids: first adapter wins.
2. `processes.discover_processes()` returns `ProcessInfo` for matching command lines
   (already redacted).
3. `correlate.correlate()` attaches processes to sessions in three deterministic
   passes (cwd == → high; shared project root → medium; recency → low), records
   evidence, then assigns status from thresholds in config.
4. Sessions are grouped into `Mission`s by `project_root` (fallback cwd); mission name
   comes from `mission_detection_rules` (folder name → git branch → markdown title).

## 3. Data model

See `models.py`. Everything undetectable stays `None` and is rendered as "unknown".
`Session.evidence` is the debugging trail — adapters and the correlator must append
evidence for anything non-obvious they conclude.

## 4. CLI architecture

`cli.py` is plain argparse; every subcommand is a `cmd_*(args, config) -> int`
function set via `set_defaults(func=…)`. Output uses rich tables/trees. To add a
command: add a parser in `build_parser()`, write `cmd_x`, keep it thin — real logic
belongs in the backend modules so the server can reuse it.

## 5. GUI architecture

`server.py` builds the FastAPI app via `create_app(config)` (test-friendly). All JSON
responses pass through `redact()`. `web/index.html` is served inline and polls
`/api/tree` every 4 s. No frontend build system — edit the HTML directly.

Endpoints: `GET /api/sessions`, `GET /api/sessions/{id}`, `GET /api/tree`,
`POST /api/sessions/{id}/open`, `GET /api/sessions/{id}/open-command`,
`GET /api/sessions/{id}/read?which=auto|markdown|log|session`,
`GET /api/doctor`, `GET /api/export`.

## 6. Adding a new detector/adapter

1. Create `csm/discovery/mytool.py`:

```python
class MyToolAdapter:
    name = "mytool"
    def discover(self, config):  # -> list[Session]
        sessions = []
        # scan config.expanded_scan_paths() or your own source
        # build Session(id=…, session_file=…, source=self.name)
        # add_evidence() for every inferred field
        return sessions
```

2. Register it in `csm/discovery/__init__.py`: `ADAPTERS = [FilesAdapter(), MyToolAdapter(), MockAdapter()]`.
3. Rules: never raise on missing paths; never invent model/usage values; respect
   `config.is_ignored()` and `max_file_size_mb`.
4. Add a test with fixture files under `tests/`.

## 7. Adding a terminal launcher

Add an entry to `TERMINAL_WHITELIST` in `launcher.py` — an argv template where
`{dir}` is substituted with the validated directory. If the terminal has no workdir
flag, list the bare command; `open_terminal()` sets the process cwd. Never include
`sh -c` or string-joined commands. Add it to the `pick_terminal` auto-order if it
should be auto-detected, and extend `tests/test_launcher.py`.

## 8. Testing with mock data

```bash
python3 -m pytest tests/ -v
# run the whole app against mock data only:
CSM_MOCK_DATA=mock_data/mock_sessions.json csm list
# or set mock_data_file in a config file
```

`mock_data/mock_sessions.json` covers: active/completed/idle/unknown status, known and
unknown model, with/without usage, and high/medium/low/unknown confidence. Tests use
isolated tmp-path configs so they never scan the real home directory.

## 9. Known limitations

- Correlation can mis-attach when several Claude processes share one cwd (first match
  wins; only one session gets the process).
- Transcript parsing pre-filters lines by substring for speed; exotic transcript
  variants with different key names would need an adapter tweak.
- `cpu_percent` is sampled instantaneously (interval=0) and reads 0.0 on first sample.
- Cost estimates ignore cache-read pricing; treat as a floor.
- The GUI has no auth: anyone with local access to the port can trigger the (safe,
  whitelisted) open action. Keep it on localhost.

## 10. Roadmap ideas

- Adapter for other agent CLIs (codex, gemini) — the model already fits.
- Persist snapshots to sqlite for history/trends of token usage.
- `csm watch` desktop notifications when a session goes idle/fails.
- WebSocket push instead of polling; collapsible tree with per-mission token totals.
- Windows/macOS terminal launchers (wt.exe, Terminal.app via `open -a`).
