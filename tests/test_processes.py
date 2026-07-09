import re

from csm.config import DEFAULT_PROCESS_PATTERNS, Config
from csm.discovery.processes import _matches, discover_processes


def test_pattern_matches_claude_invocations():
    pats = DEFAULT_PROCESS_PATTERNS
    assert _matches("claude --continue", pats)
    assert _matches("/usr/local/bin/claude", pats)
    assert _matches("node /opt/@anthropic-ai/claude-code/cli.js", pats)


def test_pattern_rejects_lookalikes():
    pats = DEFAULT_PROCESS_PATTERNS
    assert not _matches("clouded-thinking.py", pats)
    assert not _matches("vim claudette.txt", pats)


def test_discover_runs_without_error():
    # Smoke test on the live system; result content depends on environment.
    procs = discover_processes(Config())
    for p in procs:
        assert p.pid > 0
        assert "sk-ant" not in p.command  # redaction applied
