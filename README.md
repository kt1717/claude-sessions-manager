# claude-session-monitor

A local, safe-by-default "top / Task Manager" for Claude Code sessions: terminal CLI/TUI
(`csm top`, `csm tree`, `csm list`, …) plus a localhost web dashboard (`csm serve`).

```bash
pip3 install --user -e . && export PATH="$HOME/.local/bin:$PATH"
csm config init && csm doctor
csm tree
csm serve   # http://127.0.0.1:8765
```

- **User guide:** [docs/USER_GUIDE.md](docs/USER_GUIDE.md)
- **Developer guide:** [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md)
- Tests: `python3 -m pytest tests/`
- Mock data for development: `CSM_MOCK_DATA=mock_data/mock_sessions.json csm list`

Everything is local: no cloud APIs, no keys, secrets redacted, nothing from session
files is ever executed, and the GUI binds 127.0.0.1 by default.
