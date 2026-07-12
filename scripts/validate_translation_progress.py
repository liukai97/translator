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
from pathlib import Path
from typing import Any

from translation_core.cli import emit_json_report
from translation_core.collections import duplicate_values as find_duplicates
from translation_core.jsonl import read_jsonl
from translation_core.paths import BATCHES_PATH, SEGMENTS_PATH, TRANSLATIONS_PATH
from translation_core.progress import (
    completed_translation_ids,
    first_pending_batch,
    missing_segment_ids,
    pending_batch_summaries,
    pending_batch_summary,
)
from translation_core.validation import require_keys


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
    expected_batch_ids = batch_ids[start : start + expect_count]
    if len(expected_batch_ids) != expect_count:
        raise ValueError(
            "--expect-count exceeds the remaining batches: "
            f"requested {expect_count}, available {len(expected_batch_ids)}"
        )
    return expected_batch_ids


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
    empty_translation_ids = sorted(
        {
            str(row["segment_id"])
            for row in translations
            if not isinstance(row["translation"], str) or not row["translation"].strip()
        }
    )
    translated_id_set = completed_translation_ids(translations)
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
    duplicate_batch_segment_ids = find_duplicates(batch_segment_ids)
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
        missing = missing_segment_ids(batch, translated_id_set)
        expected_segment_count += len(batch["segment_ids"])
        if missing:
            expected_missing_by_batch[batch_id] = missing

    pending_batches = (
        pending_batch_summaries(batches, translated_id_set, limit=list_pending)
        if list_pending > 0
        else []
    )
    next_pending = first_pending_batch(batches, translated_id_set)
    next_missing_batch = (
        pending_batch_summary(next_pending, translated_id_set)
        if next_pending is not None
        else None
    )

    fatal_issues = {
        "duplicate_segment_ids": duplicate_segment_ids,
        "duplicate_translation_ids": duplicate_translation_ids,
        "duplicate_batch_ids": duplicate_batch_ids,
        "duplicate_batch_segment_ids": duplicate_batch_segment_ids,
        "batch_segment_ids_not_in_segments": batch_segment_ids_not_in_segments,
        "segment_ids_not_in_batches": segment_ids_not_in_batches,
        "unmatched_translation_ids": unmatched_translation_ids,
        "empty_translation_ids": empty_translation_ids,
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
        "next_missing_batch": next_missing_batch,
        "pending_batches": pending_batches,
        "checkpoint": {
            "expected_batch_ids": expected_batch_ids,
            "expected_batch_count": len(expected_batch_ids),
            "expected_segment_count": expected_segment_count,
            "missing_by_batch": expected_missing_by_batch,
        },
        "issues": {
            **fatal_issues,
        },
    }
    return report, ok


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate translation JSONL progress and checkpoint coverage."
    )
    parser.add_argument(
        "--segments",
        default=SEGMENTS_PATH,
        type=Path,
        help="Source segments JSONL. Default: work/segments.jsonl",
    )
    parser.add_argument(
        "--batches",
        default=BATCHES_PATH,
        type=Path,
        help="Translation batches JSONL. Default: work/batches.jsonl",
    )
    parser.add_argument(
        "--translations",
        default=TRANSLATIONS_PATH,
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
    emit_json_report(report)
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
