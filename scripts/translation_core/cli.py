"""Shared command-line output helpers."""

from __future__ import annotations

import json
from typing import Any


def emit_json_report(report: Any) -> None:
    """Print one compact UTF-8-friendly JSON report line."""
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
