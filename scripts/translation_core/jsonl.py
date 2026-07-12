"""JSON Lines input and output helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_jsonl(
    path: Path,
    required: bool = True,
    *,
    encoding: str = "utf-8",
    require_object: bool = True,
    line_number_key: str | None = None,
) -> list[Any]:
    if not path.exists():
        if required:
            raise FileNotFoundError(path)
        return []

    rows: list[Any] = []
    with path.open("r", encoding=encoding) as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if require_object and not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: row must be a JSON object")
            if line_number_key is not None:
                if not isinstance(row, dict):
                    raise ValueError(f"{path}:{line_number}: row must be a JSON object")
                row[line_number_key] = line_number
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    write_jsonl(temporary_path, rows)
    temporary_path.replace(path)
