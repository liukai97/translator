"""Validate and normalize one batch of model translation output.

Accept a JSON array, a JSON object containing ``translations``, a single JSON
object, or JSONL. Require the output IDs to match exactly the still-missing
segments in one batch, write canonical JSONL in source order, and optionally
append it through ``append_translations.py``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def read_jsonl(path: Path, required: bool = True) -> list[dict[str, Any]]:
    if not path.exists():
        if required:
            raise FileNotFoundError(path)
        return []

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as file:
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


def duplicate_values(values: list[str]) -> list[str]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


def strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 3 or not lines[-1].strip().startswith("```"):
        raise ValueError("model output starts a Markdown fence but does not close it")
    return "\n".join(lines[1:-1]).strip()


def parse_model_output(path: Path) -> list[dict[str, Any]]:
    text = strip_markdown_fence(path.read_text(encoding="utf-8-sig"))
    if not text:
        raise ValueError(f"model output is empty: {path}")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSONL: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: JSONL row must be an object")
            rows.append(row)
        return rows

    if isinstance(parsed, list):
        rows = parsed
    elif isinstance(parsed, dict) and isinstance(parsed.get("translations"), list):
        rows = parsed["translations"]
    elif isinstance(parsed, dict) and "segment_id" in parsed:
        rows = [parsed]
    else:
        raise ValueError(
            "model output must be a JSON array, JSONL, a single translation object, "
            "or an object containing a translations array"
        )

    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("every model output item must be a JSON object")
    return rows


def load_expected_ids(
    batches: list[dict[str, Any]],
    translations: list[dict[str, Any]],
    batch_id: str,
) -> tuple[list[str], dict[str, str]]:
    batch_ids = [str(batch.get("batch_id", "")) for batch in batches]
    duplicate_batch_ids = duplicate_values(batch_ids)
    if duplicate_batch_ids:
        raise ValueError(f"duplicate batch IDs: {', '.join(duplicate_batch_ids[:20])}")

    source_ids = [
        str(segment_id)
        for batch in batches
        for segment_id in batch.get("segment_ids", [])
    ]
    duplicate_source_ids = duplicate_values(source_ids)
    if duplicate_source_ids:
        raise ValueError(f"duplicate batch segment IDs: {', '.join(duplicate_source_ids[:20])}")

    translation_ids = [str(row.get("segment_id", "")) for row in translations]
    duplicate_translation_ids = duplicate_values(translation_ids)
    if duplicate_translation_ids:
        raise ValueError(
            "translations contains duplicate IDs: " + ", ".join(duplicate_translation_ids[:20])
        )
    unknown_translation_ids = sorted(set(translation_ids) - set(source_ids))
    if unknown_translation_ids:
        raise ValueError(
            "translations contains IDs outside the current batch data: "
            + ", ".join(unknown_translation_ids[:20])
        )

    selected = next(
        (batch for batch in batches if str(batch.get("batch_id", "")) == batch_id),
        None,
    )
    if selected is None:
        raise ValueError(f"batch not found: {batch_id}")

    declared_ids = [str(segment_id) for segment_id in selected.get("segment_ids", [])]
    embedded_segments = selected.get("segments", [])
    embedded_ids = [str(segment.get("id", "")) for segment in embedded_segments]
    if declared_ids != embedded_ids:
        raise ValueError(f"batch {batch_id} segment_ids do not match embedded segments")
    source_by_id = {
        str(segment["id"]): str(segment.get("source", ""))
        for segment in embedded_segments
    }

    translated_ids = set(translation_ids)
    expected_ids = [
        str(segment_id)
        for segment_id in selected["segment_ids"]
        if str(segment_id) not in translated_ids
    ]
    if not expected_ids:
        raise ValueError(f"batch is already fully translated: {batch_id}")
    return expected_ids, source_by_id


def normalize_rows(
    rows: list[dict[str, Any]],
    expected_ids: list[str],
    source_by_id: dict[str, str],
) -> tuple[list[dict[str, str]], bool]:
    if not rows:
        raise ValueError("model output contains no translation rows")

    output_ids = [str(row.get("segment_id", "")) for row in rows]
    missing_segment_id_lines = [
        str(index) for index, segment_id in enumerate(output_ids, start=1) if not segment_id
    ]
    if missing_segment_id_lines:
        raise ValueError(
            "model output rows missing segment_id at positions: "
            + ", ".join(missing_segment_id_lines[:20])
        )
    duplicate_output_ids = duplicate_values(output_ids)
    if duplicate_output_ids:
        raise ValueError(
            "model output contains duplicate IDs: " + ", ".join(duplicate_output_ids[:20])
        )

    expected_set = set(expected_ids)
    output_set = set(output_ids)
    missing_ids = [segment_id for segment_id in expected_ids if segment_id not in output_set]
    extra_ids = [segment_id for segment_id in output_ids if segment_id not in expected_set]
    if missing_ids or extra_ids:
        raise ValueError(
            "model output IDs do not exactly match the batch targets; "
            f"missing={missing_ids[:20]}, extra={extra_ids[:20]}"
        )

    by_id: dict[str, dict[str, str]] = {}
    for position, row in enumerate(rows, start=1):
        segment_id = str(row["segment_id"])
        translation = row.get("translation")
        if not isinstance(translation, str) or not translation.strip():
            raise ValueError(f"model output row {position} ({segment_id}) has an empty translation")

        normalized_translation = translation.replace("\r\n", "\n").replace("\r", "\n").strip()
        normalized_source = source_by_id[segment_id].replace("\r\n", "\n").replace("\r", "\n")
        source_line_breaks = normalized_source.count("\n")
        translation_line_breaks = normalized_translation.count("\n")
        if translation_line_breaks != source_line_breaks:
            raise ValueError(
                f"model output row {position} ({segment_id}) line break count mismatch: "
                f"source={source_line_breaks}, translation={translation_line_breaks}; "
                "preserve internal line breaks as escaped \\n inside the JSON string"
            )

        status = str(row.get("status") or "translated")
        if status not in {"translated", "needs_review"}:
            raise ValueError(
                f"model output row {position} ({segment_id}) has unsupported status: {status}"
            )

        normalized: dict[str, str] = {
            "segment_id": segment_id,
            "translation": normalized_translation,
            "status": status,
        }
        if status == "needs_review":
            review_note = row.get("review_note")
            if not isinstance(review_note, str) or not review_note.strip():
                raise ValueError(
                    f"model output row {position} ({segment_id}) needs a non-empty review_note"
                )
            normalized["review_note"] = review_note.strip()
        by_id[segment_id] = normalized

    was_reordered = output_ids != expected_ids
    return [by_id[segment_id] for segment_id in expected_ids], was_reordered


def write_jsonl_atomic(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary_path.replace(path)


def append_merged_output(
    append_script: Path,
    merged_path: Path,
    segments_path: Path,
    translations_path: Path,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(append_script),
        str(merged_path),
        "--segments",
        str(segments_path),
        "--translations",
        str(translations_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"append_translations.py failed: {details}")
    output_lines = [line for line in completed.stdout.splitlines() if line.strip()]
    return json.loads(output_lines[-1]) if output_lines else {"ok": True}


def default_merged_path(model_output: Path) -> Path:
    if model_output.suffix:
        return model_output.with_name(f"{model_output.stem}_merged.jsonl")
    return model_output.with_name(f"{model_output.name}_merged.jsonl")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and normalize one batch of model JSON/JSONL output."
    )
    parser.add_argument("model_output", type=Path)
    parser.add_argument("--batch", required=True, help="Batch ID represented by the model output.")
    parser.add_argument("--batches", default=Path("work/batches.jsonl"), type=Path)
    parser.add_argument("--segments", default=Path("work/segments.jsonl"), type=Path)
    parser.add_argument("--translations", default=Path("work/translations.jsonl"), type=Path)
    parser.add_argument("--output", type=Path, help="Canonical merged JSONL output path.")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append the canonical output to translations.jsonl after validation.",
    )
    parser.add_argument(
        "--append-script",
        default=Path("scripts/append_translations.py"),
        type=Path,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    batches = read_jsonl(args.batches)
    translations = read_jsonl(args.translations, required=False)
    expected_ids, source_by_id = load_expected_ids(batches, translations, args.batch)
    raw_rows = parse_model_output(args.model_output)
    normalized_rows, was_reordered = normalize_rows(raw_rows, expected_ids, source_by_id)

    merged_path = args.output or default_merged_path(args.model_output)
    write_jsonl_atomic(merged_path, normalized_rows)

    append_report = None
    if args.append:
        append_report = append_merged_output(
            append_script=args.append_script,
            merged_path=merged_path,
            segments_path=args.segments,
            translations_path=args.translations,
        )

    report = {
        "ok": True,
        "batch_id": args.batch,
        "model_output_path": str(args.model_output),
        "merged_output_path": str(merged_path),
        "validated_rows": len(normalized_rows),
        "first_segment_id": expected_ids[0],
        "last_segment_id": expected_ids[-1],
        "reordered": was_reordered,
        "appended": args.append,
        "append_report": append_report,
    }
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
