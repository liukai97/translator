"""Strict collection-level validation for canonical translation data."""

from __future__ import annotations

from typing import Any

from translation_core.collections import duplicate_values


SEGMENT_REQUIRED_KEYS = frozenset({"id", "chapter_id", "order", "kind", "source"})
BATCH_REQUIRED_KEYS = frozenset({"batch_id", "chapter_id", "segment_ids", "segments"})
TRANSLATION_REQUIRED_KEYS = frozenset({"segment_id", "translation"})


def require_keys(
    rows: list[dict[str, Any]],
    keys: set[str] | frozenset[str],
    label: str,
) -> None:
    for row_number, row in enumerate(rows, start=1):
        missing = sorted(keys - set(row))
        if missing:
            raise ValueError(f"{label} row {row_number} missing keys: {', '.join(missing)}")


def validate_segments(segments: list[dict[str, Any]]) -> list[str]:
    require_keys(segments, SEGMENT_REQUIRED_KEYS, "segment")
    segment_ids = [str(segment["id"]) for segment in segments]
    empty_ids = [str(index) for index, value in enumerate(segment_ids, start=1) if not value]
    if empty_ids:
        raise ValueError("segment rows have empty ids at lines: " + ", ".join(empty_ids[:20]))
    duplicates = duplicate_values(segment_ids)
    if duplicates:
        raise ValueError(f"duplicate segment ids: {', '.join(duplicates[:20])}")
    return segment_ids


def validate_batches(
    batches: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    require_keys(batches, BATCH_REQUIRED_KEYS, "batch")
    batch_ids = [str(batch["batch_id"]) for batch in batches]
    empty_batch_ids = [str(index) for index, value in enumerate(batch_ids, start=1) if not value]
    if empty_batch_ids:
        raise ValueError("batch rows have empty batch_id at lines: " + ", ".join(empty_batch_ids[:20]))
    duplicate_batch_ids = duplicate_values(batch_ids)
    if duplicate_batch_ids:
        raise ValueError(f"duplicate batch IDs: {', '.join(duplicate_batch_ids[:20])}")

    segment_ids: list[str] = []
    for batch in batches:
        batch_id = str(batch["batch_id"])
        if not isinstance(batch["segment_ids"], list):
            raise ValueError(f"batch {batch_id} segment_ids must be a list")
        if not isinstance(batch["segments"], list) or not all(
            isinstance(segment, dict) for segment in batch["segments"]
        ):
            raise ValueError(f"batch {batch_id} segments must be a list of objects")
        declared_ids = [str(value) for value in batch["segment_ids"]]
        embedded_ids = [str(segment.get("id", "")) for segment in batch["segments"]]
        if declared_ids != embedded_ids:
            raise ValueError(f"batch {batch_id} segment_ids do not match embedded segments")
        segment_ids.extend(declared_ids)

    duplicate_segment_ids = duplicate_values(segment_ids)
    if duplicate_segment_ids:
        raise ValueError(
            "duplicate batch segment IDs: " + ", ".join(duplicate_segment_ids[:20])
        )
    return batch_ids, segment_ids


def validate_translations(
    translations: list[dict[str, Any]],
    *,
    known_segment_ids: set[str] | None = None,
    require_nonempty: bool = True,
) -> list[str]:
    require_keys(translations, TRANSLATION_REQUIRED_KEYS, "translation")
    translation_ids = [str(row["segment_id"]) for row in translations]
    empty_id_lines = [
        str(index) for index, value in enumerate(translation_ids, start=1) if not value
    ]
    if empty_id_lines:
        raise ValueError(
            "translation rows missing segment_id at lines: " + ", ".join(empty_id_lines[:20])
        )
    duplicates = duplicate_values(translation_ids)
    if duplicates:
        raise ValueError("translations contains duplicate IDs: " + ", ".join(duplicates[:20]))
    if known_segment_ids is not None:
        unknown_ids = sorted(set(translation_ids) - known_segment_ids)
        if unknown_ids:
            raise ValueError(
                "translations contains IDs outside the current source data: "
                + ", ".join(unknown_ids[:20])
            )
    if require_nonempty:
        empty_translation_ids = [
            str(row["segment_id"])
            for row in translations
            if not isinstance(row.get("translation"), str) or not row["translation"].strip()
        ]
        if empty_translation_ids:
            raise ValueError(
                "translations contains empty entries: "
                + ", ".join(empty_translation_ids[:20])
            )
    return translation_ids
