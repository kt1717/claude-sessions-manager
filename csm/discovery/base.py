"""Adapter interface. New detectors implement SessionAdapter and are added to
csm.discovery.ADAPTERS (see developer guide)."""
from __future__ import annotations

from typing import Protocol

from ..config import Config
from ..models import Session


class SessionAdapter(Protocol):
    name: str

    def discover(self, config: Config) -> list[Session]:
        """Return sessions this adapter can see. Must not raise on missing paths."""
        ...
