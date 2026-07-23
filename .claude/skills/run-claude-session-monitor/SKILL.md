---
name: run-claude-session-monitor
description: Build, run, and drive claude-session-monitor (csm) — the FastAPI + Cytoscape web dashboard and CLI for monitoring Claude Code sessions. Use when asked to start csm, run its tests, take a screenshot of its dashboard, or interact with the running mindmap/classic UI (expand a task, select a session, toggle "show archived").
---

`csm` is a FastAPI backend (`csm/server.py`) serving two static-HTML dashboards
(`csm/web/index.html` — Cytoscape mindmap, `csm/web/classic.html` — list view).
It's driven headlessly via `.claude/skills/run-claude-session-monitor/driver.py`,
a ~250-line script that talks to Chrome DevTools Protocol (CDP) directly over a
websocket — no Playwright/Puppeteer/chromium-cli installed in this environment,
so the driver was written from scratch using Python's `websockets` package.

All paths below are relative to this repo root (`~/claude-session-monitor`).

## Prerequisites

```bash
pip3 install --user -e ".[web,dev]"   # fastapi, uvicorn, pydantic, psutil, rich,
                                       # pyyaml, pytest, httpx
pip3 install --user websockets        # driver-only dependency, not an app dependency
```

Needs a Chrome/Chromium binary on `PATH` (`google-chrome`, `chromium-browser`, or
`chromium`) for the driver. `google-chrome` is what's installed here.

## Build

No separate build step — pure Python, static HTML/JS, one vendored file
(`csm/web/vendor/cytoscape.min.js`, already committed).

## Run (agent path)

1. **Launch csm serve isolated and reproducible** — mock data instead of scanning
   this machine's real `~/.claude/projects`, and a scratch config so nothing
   touches your real `~/.config/claude-session-monitor/labels.json`:

   ```bash
   mkdir -p /tmp/csm-demo-config
   cat > /tmp/csm-demo-config/config.yaml <<'EOF'
   scan_paths: []
   enable_gui_actions: true
   EOF
   CSM_MOCK_DATA=mock_data/mock_sessions.json \
     nohup python3 -m csm.cli --config /tmp/csm-demo-config/config.yaml serve --port 8798 \
     > /tmp/csm-demo.log 2>&1 &
   disown
   sleep 3
   curl -sf http://127.0.0.1:8798/api/doctor   # {"sessions_found":5,...} confirms it's up
   ```

   `--config` is a **top-level** flag — it must come before `serve`, not after
   (`csm --config PATH serve --port N`, not `csm serve --config PATH`).

2. **Drive it** with the CDP driver — pipe a script to stdin, one command per line:

   ```bash
   python3 .claude/skills/run-claude-session-monitor/driver.py --session demo --port 9391 <<'EOF'
   nav http://127.0.0.1:8798/
   wait-for document.querySelector('#cards').children.length > 0
   screenshot 01-loaded
   click-node m:/tmp/mock/sonic-tests
   wait-for cy.getElementById('s:mock-bbb-active-unknown-model').length > 0
   screenshot 02-task-expanded
   click-node s:mock-bbb-active-unknown-model
   wait-for document.querySelector('#detail dl') !== null
   screenshot 03-session-detail
   set-checked #showArchived 1
   screenshot 04-show-archived-on
   console-errors
   quit
   EOF
   ```

   Screenshots land in `.claude/skills/run-claude-session-monitor/sessions/<name>/screenshots/`,
   with `screenshot.png` symlinked to the most recent one.

3. **Stop the server**: `fuser -k 8798/tcp` (kills by socket owner — safe). **Do
   NOT** `pkill -f` on a pattern containing the port number or any other
   literal text that also appears in the shell command invoking it — see
   Gotchas, this is not a theoretical warning.

### Driver commands

| command | what it does |
|---|---|
| `nav <url>` | navigate, wait for `document.readyState==='complete'` |
| `wait-for <js-bool-expr>` | poll up to 15s until truthy |
| `sleep <seconds>` | flat pause — last resort |
| `screenshot <label>` | save PNG, update the `screenshot.png` symlink |
| `click <css-selector>` | `document.querySelector(sel).click()` — for real DOM elements (buttons, the classic-view rows, nav links) |
| `set-checked <selector> <0\|1>` | set a checkbox and dispatch `change` (used for `#showArchived`) |
| `fill <selector> <text>` | set `.value` and dispatch `input` (the search box) |
| `click-node <cy-id>` | `cy.getElementById(id).emit('tap')` — **required** for graph nodes; see Gotchas |
| `rightclick-node <cy-id>` | `.emit('cxttap')` — our rename gesture. **Don't drive this** without dialog handling; see Gotchas |
| `eval <js-expr>` | print the JSON-serialized result |
| `console-errors` | print any `console.error(...)` calls seen so far (empty array = clean) |
| `quit` | close Chrome and exit |

Node/edge ids in the mindmap: `root`, `m:<project_root>` for a task, `s:<session-id>`
for a session (see `csm/web/index.html`'s `buildElements()`).

## Run (human path)

```bash
csm serve   # http://127.0.0.1:8765, real ~/.claude/projects data, Ctrl-C to stop
```
Open in a real browser. Nothing here is useful in a headless container — use the
agent path above instead.

## Test

```bash
python3 -m pytest tests/ -q
```
84 passed, ~1.7s, one `StarletteDeprecationWarning` (harmless — `httpx` vs `httpx2`
in `TestClient`).

## Gotchas

- **`pkill -f "<pattern>"` can kill the shell running the command that contains
  the pattern.** This harness runs your whole command through something like
  `bash -c "eval '<your entire multi-line script>'"`. If your `pkill -f` pattern
  (e.g. a port number used elsewhere in the same command block) also appears
  literally in *that* invoking process's own command line, `pkill -f` matches
  and kills itself — mid-script, with no error beyond a bare exit code 144. This
  cost real time to diagnose (looked exactly like Chrome silently refusing to
  start). Use `fuser -k <port>/tcp` (matches by socket ownership, not text) or
  kill specific PIDs you already have in hand — never a text pattern that might
  echo the invoking command back at itself.
- **Cytoscape nodes are canvas-drawn, not DOM elements.** A CSS-selector `click`
  or `Input.dispatchMouseEvent` at pixel coordinates will not reach them
  reliably. `cy.getElementById(id).emit('tap')` fires the *exact* event our
  code listens for (`cy.on('tap', 'node[kind="session"]', ...)`) — it's not a
  simulation-of-a-click, it's the same event a real click produces, which is
  why it's the driver's primary interaction method for the graph.
- **`Page.navigate`'s CDP reply can take anywhere from ~3s to ~20s+** in this
  sandbox, especially with many Chrome processes already running from prior
  driver invocations. The driver's `send()` timeout is 60s to absorb this —
  don't lower it. If you see `TimeoutError: CDP call Page.navigate timed out`
  at a much shorter custom timeout, this is why.
- **`rightclick-node` triggers `window.prompt()`** for our rename flow, which
  blocks JS execution until a CDP `Page.handleJavaScriptDialog` call resolves
  it. The driver doesn't implement dialog handling (out of scope for this
  pass) — driving `rightclick-node` as-is will hang the `eval_js` call. If you
  need to test rename, do it directly against the API instead:
  `curl -X POST http://127.0.0.1:8798/api/sessions/<id>/rename -d '{"name":"x"}' -H 'Content-Type: application/json'`.
- **Archive/rename mutate `labels.json` next to the config file** — always
  point `--config` at a scratch file (as in step 1 above) so driver runs never
  touch `~/.config/claude-session-monitor/labels.json`.
- **`f"...}}...` vs `"...}}..."` (f-string vs plain-string brace escaping)** —
  the driver builds JS snippets by concatenating adjacent Python string
  literals, some f-strings (need `{{`/`}}` to emit a literal brace) and some
  plain (need single `{`/`}`). Mixing them up produces a stray brace and a
  `SyntaxError: Unexpected token '}'` from `Runtime.evaluate` that only shows
  up at runtime, not at the Python level. Bit us once in `set-checked`;
  fixed — but the same class of bug can recur if the command set grows.

## Troubleshooting

- **`RuntimeError: Chrome DevTools endpoint never came up on port <N>`** — a
  stale Chrome instance may already hold that port with a locked profile dir.
  Pick a different `--port`; the driver already uses a per-session,
  per-port-unique `/tmp/csm-driver-profile-<session>-<port>` dir so this is
  rare, but reusing the same `--session`/`--port` pair right after a crash can
  still race with cleanup.
- **`csm: error: unrecognized arguments: --config ...`** — `--config` was
  placed after the subcommand. It's a top-level flag: `csm --config PATH serve`,
  not `csm serve --config PATH`.
- **`ModuleNotFoundError: No module named 'websockets'`** — `pip3 install --user
  websockets`. It's driver-only, deliberately not in `pyproject.toml` (the app
  itself has no websocket dependency).
