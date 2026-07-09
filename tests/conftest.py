import json
from pathlib import Path

import pytest

from csm.config import Config

REPO = Path(__file__).resolve().parents[1]
MOCK_FILE = REPO / "mock_data" / "mock_sessions.json"


@pytest.fixture
def config(tmp_path):
    """Config isolated to tmp_path — no real home scanning, no processes."""
    return Config(scan_paths=[str(tmp_path / "scan")], mock_data_file="")


@pytest.fixture
def mock_config(tmp_path):
    return Config(scan_paths=[str(tmp_path / "scan")], mock_data_file=str(MOCK_FILE))


@pytest.fixture
def transcript(tmp_path):
    """Write a realistic minimal Claude Code transcript; return its path."""
    scan = tmp_path / "scan" / "-home-user-proj"
    scan.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir()
    lines = [
        {"type": "user", "cwd": str(proj), "gitBranch": "feature/x",
         "sessionId": "abc123", "timestamp": "2026-07-08T10:00:00.000Z",
         "message": {"role": "user", "content": "do the thing"}},
        {"type": "assistant", "timestamp": "2026-07-08T10:01:00.000Z",
         "message": {"role": "assistant", "model": "claude-sonnet-5",
                      "usage": {"input_tokens": 10, "cache_creation_input_tokens": 90,
                                "output_tokens": 40}}},
        {"type": "assistant", "aiTitle": "Do the thing",
         "timestamp": "2026-07-08T10:02:00.000Z",
         "message": {"role": "assistant", "model": "claude-sonnet-5",
                      "usage": {"input_tokens": 5, "output_tokens": 20}}},
    ]
    f = scan / "abc123.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in lines))
    return f
