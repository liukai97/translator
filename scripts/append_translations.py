"""Safely append translated JSONL rows to work/translations.jsonl."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from translation_core.cli import emit_json_report
from translation_core.collections import duplicate_values
from translation_core.jsonl import read_jsonl as _read_jsonl
from translation_core.paths import SEGMENTS_PATH, TRANSLATIONS_PATH
from translation_core.translations import normalize_translation_row


def read_jsonl(path: Path, required: bool = True) -> list[dict[str, Any]]:
    return _read_jsonl(path, required, line_number_key="_line_number")


def file_ends_with_newline(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return True
    with path.open("rb") as file:
        file.seek(-1, 2)
        return file.read(1) == b"\n"


def validate_rows(
    draft_rows: list[dict[str, Any]],
    source_order: dict[str, int],
    source_by_id: dict[str, str],
    existing_ids: set[str],
    allow_existing: bool,
) -> tuple[list[dict[str, str]], list[str]]:
    if not draft_rows:
        raise ValueError("draft contains no translation rows")

    draft_ids = [str(row.get("segment_id", "")) for row in draft_rows]
    missing_ids = [str(row.get("_line_number")) for row in draft_rows if not row.get("segment_id")]
    if missing_ids:
        raise ValueError(f"draft rows missing segment_id at lines: {', '.join(missing_ids)}")

    duplicates = duplicate_values(draft_ids)
    if duplicates:
        raise ValueError(f"duplicate segment_id values in draft: {', '.join(duplicates[:20])}")

    unknown = [segment_id for segment_id in draft_ids if segment_id not in source_order]
    if unknown:
        raise ValueError(f"draft segment_id values not found in source: {', '.join(unknown[:20])}")

    already_translated = [segment_id for segment_id in draft_ids if segment_id in existing_ids]
    if already_translated and not allow_existing:
        raise ValueError(
            "draft includes already translated segment_id values: "
            + ", ".join(already_translated[:20])
        )

    order_positions = [source_order[segment_id] for segment_id in draft_ids]
    if order_positions != sorted(order_positions):
        raise ValueError("draft rows are not in source segment order")

    cleaned_rows: list[dict[str, str]] = []
    skipped_existing_ids: list[str] = []
    for row in draft_rows:
        line_number = row["_line_number"]
        segment_id = str(row["segment_id"])
        normalized = normalize_translation_row(
            row,
            segment_id=segment_id,
            source=source_by_id[segment_id],
            location=f"draft line {line_number}",
        )
        if segment_id in existing_ids:
            skipped_existing_ids.append(segment_id)
            continue
        cleaned_rows.append(normalized)
    return cleaned_rows, skipped_existing_ids


def append_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    needs_leading_newline = not file_ends_with_newline(path)
    with path.open("a", encoding="utf-8", newline="\n") as file:
        if needs_leading_newline:
            file.write("\n")
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and append translated JSONL draft rows.")
    parser.add_argument("draft", type=Path, help="Draft JSONL containing translated rows.")
    parser.add_argument("--segments", default=SEGMENTS_PATH, type=Path)
    parser.add_argument("--translations", default=TRANSLATIONS_PATH, type=Path)
    parser.add_argument(
        "--allow-existing",
        action="store_true",
        help="Skip draft rows whose IDs are already present instead of failing; never append duplicates.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate only; do not append.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    segments = read_jsonl(args.segments)
    existing = read_jsonl(args.translations, required=False)
    draft = read_jsonl(args.draft)

    source_ids = [str(row.get("id", "")) for row in segments]
    source_duplicates = duplicate_values(source_ids)
    if source_duplicates:
        raise ValueError(f"source contains duplicate segment IDs: {', '.join(source_duplicates[:20])}")

    existing_ids = [str(row.get("segment_id", "")) for row in existing if row.get("segment_id")]
    existing_duplicates = duplicate_values(existing_ids)
    if existing_duplicates:
        raise ValueError(f"translations already contains duplicate IDs: {', '.join(existing_duplicates[:20])}")

    source_order = {segment_id: index for index, segment_id in enumerate(source_ids)}
    source_by_id = {
        str(row.get("id", "")): str(row.get("source", ""))
        for row in segments
    }
    cleaned_rows, skipped_existing_ids = validate_rows(
        draft_rows=draft,
        source_order=source_order,
        source_by_id=source_by_id,
        existing_ids=set(existing_ids),
        allow_existing=args.allow_existing,
    )

    if not args.dry_run:
        append_rows(args.translations, cleaned_rows)

    report = {
        "ok": True,
        "dry_run": args.dry_run,
        "appended_rows": 0 if args.dry_run else len(cleaned_rows),
        "validated_rows": len(draft),
        "skipped_existing_rows": len(skipped_existing_ids),
        "first_segment_id": cleaned_rows[0]["segment_id"] if cleaned_rows else None,
        "last_segment_id": cleaned_rows[-1]["segment_id"] if cleaned_rows else None,
        "translations": str(args.translations),
    }
    emit_json_report(report)


if __name__ == "__main__":
    main()
