"""Shared translation-completion and pending-batch calculations."""

from __future__ import annotations

from collections.abc import Set
from typing import Any


def completed_translation_ids(translations: list[dict[str, Any]]) -> set[str]:
    return {
        str(row["segment_id"])
        for row in translations
        if row.get("segment_id")
        and isinstance(row.get("translation"), str)
        and row["translation"].strip()
    }


def missing_segment_ids(
    batch: dict[str, Any],
    completed_ids: Set[str],
) -> list[str]:
    return [
        str(segment_id)
        for segment_id in batch["segment_ids"]
        if str(segment_id) not in completed_ids
    ]


def first_pending_batch(
    batches: list[dict[str, Any]],
    completed_ids: Set[str],
) -> dict[str, Any] | None:
    return next(
        (batch for batch in batches if missing_segment_ids(batch, completed_ids)),
        None,
    )


def pending_batch_summary(
    batch: dict[str, Any],
    completed_ids: Set[str],
) -> dict[str, Any] | None:
    missing = missing_segment_ids(batch, completed_ids)
    if not missing:
        return None
    return {
        "batch_id": str(batch["batch_id"]),
        "missing_count": len(missing),
        "first_missing_segment_id": missing[0],
        "last_missing_segment_id": missing[-1],
        "segment_count": len(batch["segment_ids"]),
    }


def pending_batch_summaries(
    batches: list[dict[str, Any]],
    completed_ids: Set[str],
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for batch in batches:
        summary = pending_batch_summary(batch, completed_ids)
        if summary is None:
            continue
        summaries.append(summary)
        if limit is not None and len(summaries) >= limit:
            break
    return summaries
