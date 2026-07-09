"""Mock adapter: loads sessions from a JSON file (for development and tests).

Enabled when config.mock_data_file is set or CSM_MOCK_DATA env var points to a file.
JSON format: {"sessions": [<Session dict>, ...]} — see mock_data/mock_sessions.json.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..config import Config
from ..models import Session


class MockAdapter:
    name = "mock"

    def discover(self, config: Config) -> list[Session]:
        path = config.mock_data_file or os.environ.get("CSM_MOCK_DATA", "")
        if not path:
            return []
        p = Path(path).expanduser()
        if not p.is_file():
            return []
        data = json.loads(p.read_text())
        sessions = []
        for raw in data.get("sessions", []):
            s = Session.model_validate(raw)
            s.source = "mock"
            sessions.append(s)
        return sessions
