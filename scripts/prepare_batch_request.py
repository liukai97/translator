"""Prepare one Markdown translation request for one pending batch.

The request is intended to be read directly by Codex. It contains a style
guide, a small same-chapter source/translation context window, the missing
segments in one batch, and the exact JSONL output path and schema.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_STYLE_GUIDE = """\
- Translate into polished, natural Simplified Chinese suitable for continuous novel reading.
- Preserve source meaning, order, speaker intent, emotional tone, pacing, and ambiguity.
- Keep names, recurring terms, honorific choices, and character voices consistent with the provided context.
- Use natural Chinese sentence rhythm and full-width Chinese punctuation; use Chinese quotation marks by default.
- Do not merge, split, omit, summarize, soften, moralize, expand, or invent source content.
- Preserve every source line break, including blank lines. The translation must have exactly the same number of `\n` characters as its source segment.
- Keep each JSONL object on one physical line and encode segment-internal line breaks as escaped `\n` characters inside `translation`.
- Preserve meaningful pauses, ellipses, hesitations, interruptions, consent cues, discomfort cues, and power dynamics.
- If a translation is genuinely uncertain, use `status: "needs_review"` and add a concise `review_note`.
"""


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


def validate_inputs(
    batches: list[dict[str, Any]],
    translations: list[dict[str, Any]],
) -> None:
    batch_ids = [str(batch.get("batch_id", "")) for batch in batches]
    duplicate_batch_ids = duplicate_values(batch_ids)
    if duplicate_batch_ids:
        raise ValueError(f"duplicate batch IDs: {', '.join(duplicate_batch_ids[:20])}")

    segment_ids: list[str] = []
    for row_number, batch in enumerate(batches, start=1):
        missing = sorted({"batch_id", "chapter_id", "segment_ids", "segments"} - set(batch))
        if missing:
            raise ValueError(f"batch row {row_number} missing keys: {', '.join(missing)}")
        declared_ids = [str(value) for value in batch["segment_ids"]]
        embedded_ids = [str(segment.get("id", "")) for segment in batch["segments"]]
        if declared_ids != embedded_ids:
            raise ValueError(f"batch {batch['batch_id']} segment_ids do not match embedded segments")
        segment_ids.extend(declared_ids)

    duplicate_segment_ids = duplicate_values(segment_ids)
    if duplicate_segment_ids:
        raise ValueError(f"duplicate batch segment IDs: {', '.join(duplicate_segment_ids[:20])}")

    translation_ids = [str(row.get("segment_id", "")) for row in translations]
    missing_translation_ids = [str(index) for index, value in enumerate(translation_ids, start=1) if not value]
    if missing_translation_ids:
        raise ValueError(
            "translation rows missing segment_id at lines: " + ", ".join(missing_translation_ids[:20])
        )
    duplicate_translation_ids = duplicate_values(translation_ids)
    if duplicate_translation_ids:
        raise ValueError(
            "translations contains duplicate IDs: " + ", ".join(duplicate_translation_ids[:20])
        )
    unknown_translation_ids = sorted(set(translation_ids) - set(segment_ids))
    if unknown_translation_ids:
        raise ValueError(
            "translations contains IDs outside the current batch data; refuse to build misleading context: "
            + ", ".join(unknown_translation_ids[:20])
        )
    empty_translation_ids = [
        str(row["segment_id"])
        for row in translations
        if not isinstance(row.get("translation"), str) or not row["translation"].strip()
    ]
    if empty_translation_ids:
        raise ValueError(
            "translations contains empty entries that must be cleaned before resuming: "
            + ", ".join(empty_translation_ids[:20])
        )


def translation_map(translations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["segment_id"]): row for row in translations}


def missing_segment_ids(
    batch: dict[str, Any],
    translated_ids: set[str],
) -> list[str]:
    return [
        str(segment_id)
        for segment_id in batch["segment_ids"]
        if str(segment_id) not in translated_ids
    ]


def select_batch(
    batches: list[dict[str, Any]],
    translated_ids: set[str],
    requested_batch_id: str | None,
) -> tuple[int, dict[str, Any]]:
    if requested_batch_id:
        for index, batch in enumerate(batches):
            if str(batch["batch_id"]) == requested_batch_id:
                if not missing_segment_ids(batch, translated_ids):
                    raise ValueError(f"batch is already fully translated: {requested_batch_id}")
                return index, batch
        raise ValueError(f"batch not found: {requested_batch_id}")

    for index, batch in enumerate(batches):
        if missing_segment_ids(batch, translated_ids):
            return index, batch
    raise ValueError("no pending translation batches")


def flatten_segments(batches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for batch in batches:
        chapter_id = str(batch["chapter_id"])
        batch_id = str(batch["batch_id"])
        for segment in batch["segments"]:
            flattened.append(
                {
                    "id": str(segment["id"]),
                    "kind": str(segment.get("kind", "paragraph")),
                    "source": str(segment.get("source", "")),
                    "chapter_id": chapter_id,
                    "batch_id": batch_id,
                }
            )
    return flattened


def select_previous_context(
    batches: list[dict[str, Any]],
    current_batch: dict[str, Any],
    current_target_ids: list[str],
    translations_by_id: dict[str, dict[str, Any]],
    context_segments: int,
) -> list[dict[str, str]]:
    if context_segments < 0:
        raise ValueError("--context-segments must be zero or positive")
    if context_segments == 0:
        return []

    flattened = flatten_segments(batches)
    position_by_id = {segment["id"]: index for index, segment in enumerate(flattened)}
    first_target_id = current_target_ids[0]
    first_target_position = position_by_id[first_target_id]
    chapter_id = str(current_batch["chapter_id"])

    selected: list[dict[str, str]] = []
    for segment in reversed(flattened[:first_target_position]):
        if segment["chapter_id"] != chapter_id:
            break
        translated = translations_by_id.get(segment["id"])
        if translated is None:
            continue
        selected.append(
            {
                "segment_id": segment["id"],
                "kind": segment["kind"],
                "source": segment["source"],
                "translation": str(translated["translation"]),
            }
        )
        if len(selected) >= context_segments:
            break
    selected.reverse()
    return selected


def load_style_guide(path: Path) -> tuple[str, str]:
    if path.exists():
        text = path.read_text(encoding="utf-8-sig").strip()
        if not text:
            raise ValueError(f"style guide is empty: {path}")
        return text, str(path)
    return DEFAULT_STYLE_GUIDE.strip(), "built-in default"


def indent_block(text: str) -> str:
    lines = text.splitlines() or [""]
    return "\n".join(f"    {line}" for line in lines)


def render_request(
    batch: dict[str, Any],
    target_segments: list[dict[str, str]],
    previous_context: list[dict[str, str]],
    style_guide: str,
    style_guide_source: str,
    output_path: Path,
    next_command: str | None,
) -> str:
    batch_id = str(batch["batch_id"])
    chapter_id = str(batch["chapter_id"])
    target_ids = [segment["id"] for segment in target_segments]

    parts = [
        "# Translation Request",
        "",
        f"- Batch ID: `{batch_id}`",
        f"- Chapter ID: `{chapter_id}`",
        f"- Target segments: `{len(target_segments)}`",
        f"- Output path: `{output_path}`",
        "",
        "## Mandatory Instructions",
        "",
        "1. Read this request completely before translating.",
        "2. Use Previous Translation Context only for continuity; do not output it again.",
        "3. Translate every segment under Translation Targets exactly once and preserve its `segment_id`.",
        "4. Do not merge, split, omit, reorder, summarize, or invent segments.",
        f"5. Write the result as UTF-8 JSONL directly to `{output_path}`.",
        "6. Write one JSON object per line with no Markdown fences or surrounding JSON array.",
        "7. Use `status: \"translated\"`; if genuinely uncertain, use `status: \"needs_review\"` and add `review_note`.",
        "8. Do not append to `work/translations.jsonl` directly; validate this batch output first.",
        "9. Preserve every line break and blank line inside each source segment; translation and source must contain the same number of `\\n` characters.",
        "10. Keep one JSON object per physical JSONL line. Represent internal translation line breaks with escaped `\\n`, never literal line breaks between JSON tokens.",
        "",
        "## Style Guide",
        "",
        f"Source: `{style_guide_source}`",
        "",
        style_guide,
        "",
        "## Previous Translation Context — Read Only",
        "",
    ]

    if previous_context:
        for row in previous_context:
            parts.extend(
                [
                    f"### {row['segment_id']} | {row['kind']}",
                    "",
                    "Japanese source:",
                    "",
                    indent_block(row["source"]),
                    "",
                    "Chinese translation:",
                    "",
                    indent_block(row["translation"]),
                    "",
                ]
            )
    else:
        parts.extend(["No completed same-chapter context is available.", ""])

    parts.extend(["## Translation Targets", ""])
    for segment in target_segments:
        parts.extend(
            [
                f"### {segment['id']} | {segment['kind']}",
                "",
                indent_block(segment["source"]),
                "",
            ]
        )

    follow_up_lines = (
        [
            "After writing the file, run the task loop helper again to submit this batch and continue:",
            "",
            "```powershell",
            next_command,
            "```",
        ]
        if next_command
        else [
            "After writing the file, validate and append it with:",
            "",
            "```powershell",
            f'.\\.python312\\python.exe scripts\\merge_model_output.py "{output_path}" --batch {batch_id} --append',
            "```",
        ]
    )
    parts.extend(
        [
            "## Required Output",
            "",
            f"Write exactly `{len(target_segments)}` JSONL rows to `{output_path}` in this order:",
            "",
            *[f"{index}. `{segment_id}`" for index, segment_id in enumerate(target_ids, start=1)],
            "",
            "Each output line must use one of these shapes:",
            "",
            "```json",
            '{"segment_id":"ch001-p0001","translation":"...","status":"translated"}',
            '{"segment_id":"ch001-p0002","translation":"...","status":"needs_review","review_note":"..."}',
            '{"segment_id":"ch001-p0003","translation":"第一行。\\n\\n第二行。","status":"translated"}',
            "```",
            "",
            *follow_up_lines,
            "",
        ]
    )
    return "\n".join(parts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare one Markdown request for translating one pending batch."
    )
    parser.add_argument("--batch", help="Batch ID. Default: first pending batch.")
    parser.add_argument("--batches", default=Path("work/batches.jsonl"), type=Path)
    parser.add_argument("--translations", default=Path("work/translations.jsonl"), type=Path)
    parser.add_argument(
        "--style-guide",
        default=Path("glossary/style_guide.md"),
        type=Path,
        help="Style guide Markdown. Uses a built-in guide when this path does not exist.",
    )
    parser.add_argument(
        "--context-segments",
        default=12,
        type=int,
        help="Maximum completed same-chapter segments to include before the first target. Default: 12",
    )
    parser.add_argument("--output-dir", default=Path("work/checkpoints"), type=Path)
    parser.add_argument("--request-output", type=Path, help="Explicit Markdown request path.")
    parser.add_argument("--model-output", type=Path, help="JSONL path that the model must write.")
    parser.add_argument(
        "--next-command",
        help="Follow-up command shown after output instructions for task-loop orchestration.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    batches = read_jsonl(args.batches)
    translations = read_jsonl(args.translations, required=False)
    validate_inputs(batches, translations)

    translations_by_id = translation_map(translations)
    batch_index, batch = select_batch(batches, set(translations_by_id), args.batch)
    target_id_set = set(missing_segment_ids(batch, set(translations_by_id)))
    target_segments = [
        {
            "id": str(segment["id"]),
            "kind": str(segment.get("kind", "paragraph")),
            "source": str(segment.get("source", "")),
        }
        for segment in batch["segments"]
        if str(segment["id"]) in target_id_set
    ]
    target_ids = [segment["id"] for segment in target_segments]
    previous_context = select_previous_context(
        batches=batches,
        current_batch=batch,
        current_target_ids=target_ids,
        translations_by_id=translations_by_id,
        context_segments=args.context_segments,
    )
    style_guide, style_guide_source = load_style_guide(args.style_guide)

    batch_id = str(batch["batch_id"])
    request_path = args.request_output or args.output_dir / f"{batch_id}_request.md"
    model_output_path = args.model_output or args.output_dir / f"{batch_id}_output.jsonl"
    request_path.parent.mkdir(parents=True, exist_ok=True)
    model_output_path.parent.mkdir(parents=True, exist_ok=True)

    request = render_request(
        batch=batch,
        target_segments=target_segments,
        previous_context=previous_context,
        style_guide=style_guide,
        style_guide_source=style_guide_source,
        output_path=model_output_path,
        next_command=args.next_command,
    )
    request_path.write_text(request, encoding="utf-8", newline="\n")

    report = {
        "ok": True,
        "batch_id": batch_id,
        "batch_index": batch_index,
        "request_path": str(request_path),
        "model_output_path": str(model_output_path),
        "style_guide_source": style_guide_source,
        "context_segment_count": len(previous_context),
        "target_segment_count": len(target_segments),
        "first_target_id": target_ids[0],
        "last_target_id": target_ids[-1],
        "merge_command": (
            f'.\\.python312\\python.exe scripts\\merge_model_output.py '
            f'"{model_output_path}" --batch {batch_id} --append'
        ),
    }
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
