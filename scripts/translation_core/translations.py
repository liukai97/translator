"""Canonical validation and normalization for persisted translation rows."""

from __future__ import annotations

from typing import Any, Mapping

from translation_core.epub_placeholders import extract_epub_image_placeholders


SUPPORTED_TRANSLATION_STATUSES = frozenset({"translated", "needs_review"})


def normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def normalize_translation_row(
    row: Mapping[str, Any],
    *,
    segment_id: str,
    source: str,
    location: str,
) -> dict[str, str]:
    """Return one canonical translation row or raise a location-aware error."""
    translation = row.get("translation")
    if not isinstance(translation, str) or not translation.strip():
        raise ValueError(f"{location} ({segment_id}) has an empty translation")

    normalized_translation = normalize_line_endings(translation).strip()
    normalized_source = normalize_line_endings(source)
    source_line_breaks = normalized_source.count("\n")
    translation_line_breaks = normalized_translation.count("\n")
    if translation_line_breaks != source_line_breaks:
        raise ValueError(
            f"{location} ({segment_id}) line break count mismatch: "
            f"source={source_line_breaks}, translation={translation_line_breaks}; "
            "preserve internal line breaks as escaped \\n inside the JSON string"
        )

    source_placeholders = extract_epub_image_placeholders(normalized_source)
    translation_placeholders = extract_epub_image_placeholders(normalized_translation)
    if translation_placeholders != source_placeholders:
        raise ValueError(
            f"{location} ({segment_id}) EPUB image placeholder mismatch: "
            f"source={len(source_placeholders)}, "
            f"translation={len(translation_placeholders)}; preserve every "
            "⟦EPUB_IMG:...⟧ token unchanged and in source order"
        )

    status = str(row.get("status") or "translated")
    if status not in SUPPORTED_TRANSLATION_STATUSES:
        raise ValueError(f"{location} ({segment_id}) has unsupported status: {status}")

    normalized = {
        "segment_id": segment_id,
        "translation": normalized_translation,
        "status": status,
    }
    if status == "needs_review":
        review_note = row.get("review_note")
        if not isinstance(review_note, str) or not review_note.strip():
            raise ValueError(f"{location} ({segment_id}) needs a non-empty review_note")
        normalized["review_note"] = review_note.strip()
    return normalized
