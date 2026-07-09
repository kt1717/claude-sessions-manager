"""Redaction of obvious secrets before anything is displayed or served."""
from __future__ import annotations

import re

REDACTED = "[REDACTED]"

_PATTERNS = [
    # Anthropic / OpenAI style API keys
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    # GitHub tokens
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    # AWS access keys
    re.compile(r"AKIA[0-9A-Z]{16}"),
    # Bearer tokens
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"),
    # key=value style secrets
    re.compile(
        r"(?i)\b(api[_-]?key|token|passwd|password|secret|auth)\b(\s*[=:]\s*)(\"[^\"]+\"|'[^']+'|\S+)"
    ),
    # Private key blocks
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    # sshpass -p <password>
    re.compile(r"(sshpass\s+-p\s+)(\"[^\"]+\"|'[^']+'|\S+)"),
]


def redact(text: str) -> str:
    """Replace obvious secrets in *text* with [REDACTED]. Never raises."""
    if not text:
        return text
    out = text
    for pat in _PATTERNS:
        if pat.groups >= 3:
            out = pat.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", out)
        elif pat.groups == 2:
            out = pat.sub(lambda m: f"{m.group(1)}{REDACTED}", out)
        else:
            out = pat.sub(REDACTED, out)
    return out
