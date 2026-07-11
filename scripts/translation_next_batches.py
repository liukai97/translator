"""Prepare the next translation checkpoint.

The script finds the next pending translation batches, writes a UTF-8 source
context file, and writes a JSONL draft skeleton with one row per missing
segment. It does not modify ``work/translations.jsonl``.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def read_jsonl(path: Path, required: bool = True) -> list[dict[str, Any]]:
    if not path.exists():
        if required:
            raise FileNotFoundError(path)
        return []

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: row must be a JSON object")
            rows.append(row)
    return rows


def batch_missing_ids(batch: dict[str, Any], translated_ids: set[str]) -> list[str]:
    return [
        str(segment_id)
        for segment_id in batch.get("segment_ids", [])
        if str(segment_id) not in translated_ids
    ]


def select_pending_batches(
    batches: list[dict[str, Any]],
    translated_ids: set[str],
    count: int,
    from_batch: str | None,
) -> list[dict[str, Any]]:
    if count < 1:
        raise ValueError("--count must be positive")

    start_index = 0
    if from_batch:
        batch_ids = [str(batch.get("batch_id", "")) for batch in batches]
        try:
            start_index = batch_ids.index(from_batch)
        except ValueError as exc:
            raise ValueError(f"--from-batch not found: {from_batch}") from exc

    selected: list[dict[str, Any]] = []
    for batch in batches[start_index:]:
        missing = batch_missing_ids(batch, translated_ids)
        if not missing:
            continue
        selected.append(batch)
        if len(selected) >= count:
            break
    return selected


def source_text_for_batch(batch: dict[str, Any], missing_ids: set[str]) -> str:
    lines = [
        f"### {batch['batch_id']}",
        f"chapter_id: {batch.get('chapter_id', '')}",
        f"missing_segments: {len(missing_ids)}",
        "",
    ]
    for segment in batch.get("segments", []):
        segment_id = str(segment.get("id", ""))
        if segment_id not in missing_ids:
            continue
        kind = str(segment.get("kind", ""))
        source = str(segment.get("source", ""))
        lines.append(f"[{segment_id} | {kind}]")
        lines.append(source)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_source_file(path: Path, selected: list[dict[str, Any]], translated_ids: set[str]) -> None:
    chunks: list[str] = []
    for batch in selected:
        missing = set(batch_missing_ids(batch, translated_ids))
        chunks.append(source_text_for_batch(batch, missing))
    path.write_text("\n".join(chunks), encoding="utf-8")


def write_skeleton_file(path: Path, selected: list[dict[str, Any]], translated_ids: set[str]) -> int:
    row_count = 0
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for batch in selected:
            missing = set(batch_missing_ids(batch, translated_ids))
            for segment_id in batch.get("segment_ids", []):
                segment_id = str(segment_id)
                if segment_id not in missing:
                    continue
                row = {
                    "segment_id": segment_id,
                    "translation": "",
                    "status": "translated",
                }
                file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                row_count += 1
    return row_count


def build_prefix(selected: list[dict[str, Any]], count: int) -> str:
    first = str(selected[0]["batch_id"])
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}_{first}_{len(selected)}b"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare source and draft files for the next pending batches.")
    parser.add_argument("--batches", default=Path("work/batches.jsonl"), type=Path)
    parser.add_argument("--translations", default=Path("work/translations.jsonl"), type=Path)
    parser.add_argument("--count", default=10, type=int, help="Number of pending batches to select. Default: 10")
    parser.add_argument("--from-batch", help="Start scanning from this batch ID.")
    parser.add_argument("--output-dir", default=Path("work/checkpoints"), type=Path)
    parser.add_argument("--prefix", help="Filename prefix for generated files.")
    parser.add_argument("--no-files", action="store_true", help="Only print the JSON report; do not write files.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    batches = read_jsonl(args.batches)
    translations = read_jsonl(args.translations, required=False)
    translated_ids = {str(row.get("segment_id", "")) for row in translations if row.get("segment_id")}

    selected = select_pending_batches(
        batches=batches,
        translated_ids=translated_ids,
        count=args.count,
        from_batch=args.from_batch,
    )
    if not selected:
        print(json.dumps({"ok": True, "selected_batches": [], "message": "no pending batches"}, ensure_ascii=False))
        return

    first_batch = str(selected[0]["batch_id"])
    prefix = args.prefix or build_prefix(selected, args.count)
    source_path = args.output_dir / f"{prefix}_source.txt"
    skeleton_path = args.output_dir / f"{prefix}_draft.jsonl"

    segment_count = sum(len(batch_missing_ids(batch, translated_ids)) for batch in selected)
    if not args.no_files:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        write_source_file(source_path, selected, translated_ids)
        written_rows = write_skeleton_file(skeleton_path, selected, translated_ids)
        if written_rows != segment_count:
            raise RuntimeError(f"internal row count mismatch: wrote {written_rows}, expected {segment_count}")

    selected_report = []
    for batch in selected:
        missing = batch_missing_ids(batch, translated_ids)
        selected_report.append(
            {
                "batch_id": str(batch["batch_id"]),
                "missing_count": len(missing),
                "first_missing_segment_id": missing[0],
                "last_missing_segment_id": missing[-1],
            }
        )

    report = {
        "ok": True,
        "first_batch": first_batch,
        "selected_batch_count": len(selected),
        "selected_segment_count": segment_count,
        "selected_batches": selected_report,
        "source_path": str(source_path) if not args.no_files else None,
        "draft_path": str(skeleton_path) if not args.no_files else None,
        "append_command": f".\\.python312\\python.exe scripts\\append_translations.py {skeleton_path}",
        "checkpoint_command": (
            f".\\.python312\\python.exe scripts\\checkpoint_translation.py "
            f"--from-batch {first_batch} --count {len(selected)}"
        ),
    }
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
