"""Text decoding and measurement helpers."""

from __future__ import annotations

from pathlib import Path


def read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def byte_count(text: str) -> int:
    return len(text.encode("utf-8"))
