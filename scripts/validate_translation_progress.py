"""Validate translation JSONL progress and checkpoint coverage.

This script is the stable QA helper for the translation workflow. It checks the
alignment layer across:

- work/segments.jsonl
- work/batches.jsonl
- work/translations.jsonl

By default it reports aggregate progress and the next batch with missing
translations. For checkpoint work, pass ``--expect-from`` and ``--expect-count``
or explicit ``--expect-batches`` to assert that specific batches are complete.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
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


def find_duplicates(values: list[str]) -> list[str]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


def require_keys(rows: list[dict[str, Any]], keys: set[str], label: str) -> None:
    for row_number, row in enumerate(rows, start=1):
        missing = sorted(keys - set(row))
        if missing:
            raise ValueError(f"{label} row {row_number} missing keys: {', '.join(missing)}")


def build_expected_batch_ids(
    batches: list[dict[str, Any]],
    expect_batches: list[str],
    expect_from: str | None,
    expect_count: int | None,
) -> list[str]:
    if expect_batches and (expect_from or expect_count is not None):
        raise ValueError("use either --expect-batches or --expect-from/--expect-count, not both")
    if expect_batches:
        return expect_batches
    if expect_from is None and expect_count is None:
        return []
    if not expect_from or expect_count is None:
        raise ValueError("--expect-from and --expect-count must be used together")
    if expect_count < 1:
        raise ValueError("--expect-count must be positive")

    batch_ids = [str(batch["batch_id"]) for batch in batches]
    try:
        start = batch_ids.index(expect_from)
    except ValueError as exc:
        raise ValueError(f"--expect-from batch not found: {expect_from}") from exc
    return batch_ids[start : start + expect_count]


def first_missing_batch(
    batches: list[dict[str, Any]], translated_ids: set[str]
) -> dict[str, Any] | None:
    for batch in batches:
        missing = [str(segment_id) for segment_id in batch["segment_ids"] if str(segment_id) not in translated_ids]
        if missing:
            return {
                "batch_id": str(batch["batch_id"]),
                "missing_count": len(missing),
                "first_missing_segment_id": missing[0],
                "last_missing_segment_id": missing[-1],
                "segment_count": len(batch["segment_ids"]),
            }
    return None


def validate(
    segments_path: Path,
    batches_path: Path,
    translations_path: Path,
    expect_batches: list[str],
    expect_from: str | None,
    expect_count: int | None,
    list_pending: int,
) -> tuple[dict[str, Any], bool]:
    segments = read_jsonl(segments_path)
    batches = read_jsonl(batches_path)
    translations = read_jsonl(translations_path, required=False)

    require_keys(segments, {"id"}, "segment")
    require_keys(batches, {"batch_id", "segment_ids"}, "batch")
    require_keys(translations, {"segment_id", "translation"}, "translation")

    segment_ids = [str(row["id"]) for row in segments]
    source_id_set = set(segment_ids)
    translation_ids = [str(row["segment_id"]) for row in translations]
    translated_id_set = set(translation_ids)
    batch_ids = [str(row["batch_id"]) for row in batches]

    duplicate_segment_ids = find_duplicates(segment_ids)
    duplicate_translation_ids = find_duplicates(translation_ids)
    duplicate_batch_ids = find_duplicates(batch_ids)

    batch_segment_ids = [
        str(segment_id)
        for batch in batches
        for segment_id in batch["segment_ids"]
    ]
    batch_segment_id_set = set(batch_segment_ids)
    batch_segment_ids_not_in_segments = sorted(batch_segment_id_set - source_id_set)
    segment_ids_not_in_batches = sorted(source_id_set - batch_segment_id_set)
    unmatched_translation_ids = sorted(translated_id_set - source_id_set)

    translated_source_ids = translated_id_set & source_id_set
    pending_source_ids = source_id_set - translated_source_ids

    expected_batch_ids = build_expected_batch_ids(
        batches,
        expect_batches=expect_batches,
        expect_from=expect_from,
        expect_count=expect_count,
    )
    known_batch_ids = set(batch_ids)
    unknown_expected_batches = sorted(set(expected_batch_ids) - known_batch_ids)
    expected_missing_by_batch: dict[str, list[str]] = {}
    expected_segment_count = 0
    for batch in batches:
        batch_id = str(batch["batch_id"])
        if batch_id not in expected_batch_ids:
            continue
        missing = [
            str(segment_id)
            for segment_id in batch["segment_ids"]
            if str(segment_id) not in translated_id_set
        ]
        expected_segment_count += len(batch["segment_ids"])
        if missing:
            expected_missing_by_batch[batch_id] = missing

    pending_batches: list[dict[str, Any]] = []
    if list_pending > 0:
        for batch in batches:
            missing = [
                str(segment_id)
                for segment_id in batch["segment_ids"]
                if str(segment_id) not in translated_id_set
            ]
            if not missing:
                continue
            pending_batches.append(
                {
                    "batch_id": str(batch["batch_id"]),
                    "missing_count": len(missing),
                    "first_missing_segment_id": missing[0],
                    "last_missing_segment_id": missing[-1],
                    "segment_count": len(batch["segment_ids"]),
                }
            )
            if len(pending_batches) >= list_pending:
                break

    fatal_issues = {
        "duplicate_segment_ids": duplicate_segment_ids,
        "duplicate_translation_ids": duplicate_translation_ids,
        "duplicate_batch_ids": duplicate_batch_ids,
        "batch_segment_ids_not_in_segments": batch_segment_ids_not_in_segments,
        "unmatched_translation_ids": unmatched_translation_ids,
        "unknown_expected_batches": unknown_expected_batches,
        "expected_missing_by_batch": expected_missing_by_batch,
    }
    ok = not any(fatal_issues.values())

    report = {
        "ok": ok,
        "segments": len(segments),
        "batches": len(batches),
        "translations": len(translations),
        "unique_translation_ids": len(translated_id_set),
        "translated_source_segments": len(translated_source_ids),
        "pending_source_segments": len(pending_source_ids),
        "next_missing_batch": first_missing_batch(batches, translated_id_set),
        "pending_batches": pending_batches,
        "checkpoint": {
            "expected_batch_ids": expected_batch_ids,
            "expected_batch_count": len(expected_batch_ids),
            "expected_segment_count": expected_segment_count,
            "missing_by_batch": expected_missing_by_batch,
        },
        "issues": {
            **fatal_issues,
            "segment_ids_not_in_batches": segment_ids_not_in_batches,
        },
    }
    return report, ok


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate translation JSONL progress and checkpoint coverage."
    )
    parser.add_argument(
        "--segments",
        default="work/segments.jsonl",
        type=Path,
        help="Source segments JSONL. Default: work/segments.jsonl",
    )
    parser.add_argument(
        "--batches",
        default="work/batches.jsonl",
        type=Path,
        help="Translation batches JSONL. Default: work/batches.jsonl",
    )
    parser.add_argument(
        "--translations",
        default="work/translations.jsonl",
        type=Path,
        help="Translations JSONL. Default: work/translations.jsonl",
    )
    parser.add_argument(
        "--expect-batches",
        nargs="*",
        default=[],
        help="Specific batch IDs that must be fully translated.",
    )
    parser.add_argument(
        "--expect-from",
        help="First batch ID in a consecutive checkpoint range.",
    )
    parser.add_argument(
        "--expect-count",
        type=int,
        help="Number of consecutive batches to validate from --expect-from.",
    )
    parser.add_argument(
        "--list-pending",
        default=5,
        type=int,
        help="Number of pending batches to include in the report. Default: 5",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report, ok = validate(
        segments_path=args.segments,
        batches_path=args.batches,
        translations_path=args.translations,
        expect_batches=args.expect_batches,
        expect_from=args.expect_from,
        expect_count=args.expect_count,
        list_pending=args.list_pending,
    )
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
