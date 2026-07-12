"""Build LLM translation batches from work/segments.jsonl.

``segments.jsonl`` is the stable alignment layer. This script groups those
segments into chapter-local batches that are large enough to give an LLM
context while still requiring translations to come back by segment id.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from translation_core.cli import emit_json_report
from translation_core.jsonl import read_jsonl as _read_jsonl
from translation_core.jsonl import write_jsonl
from translation_core.paths import BATCHES_PATH, SEGMENTS_PATH
from translation_core.text import byte_count
from translation_core.validation import validate_batches as validate_batch_rows
from translation_core.validation import validate_segments


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return _read_jsonl(path, require_object=False)


def compact_segment(segment: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(segment["id"]),
        "kind": str(segment["kind"]),
        "source": str(segment["source"]),
    }


def format_source_text(segments: list[dict[str, Any]]) -> str:
    blocks = []
    for segment in segments:
        blocks.append(
            f"[{segment['id']} | {segment['kind']}]\n{segment['source']}"
        )
    return "\n\n".join(blocks)


def build_batches(
    segments: list[dict[str, Any]],
    max_bytes: int,
    max_segments: int,
    max_chars: int | None = None,
) -> list[dict[str, Any]]:
    if max_bytes < 1:
        raise ValueError("--max-bytes must be positive")
    if max_segments < 1:
        raise ValueError("--max-segments must be positive")
    if max_chars is not None and max_chars < 1:
        raise ValueError("--max-chars must be positive")

    batches: list[dict[str, Any]] = []
    current_segments: list[dict[str, Any]] = []
    current_chapter_id: str | None = None
    current_source_bytes = 0
    current_source_chars = 0
    chapter_batch_order = 0
    global_batch_order = 0

    def flush() -> None:
        nonlocal chapter_batch_order, global_batch_order, current_segments
        nonlocal current_source_bytes, current_source_chars
        if not current_segments:
            return

        chapter_id = str(current_segments[0]["chapter_id"])
        chapter_batch_order += 1
        global_batch_order += 1
        compact_segments = [compact_segment(segment) for segment in current_segments]
        segment_ids = [segment["id"] for segment in compact_segments]
        char_count = sum(len(segment["source"]) for segment in compact_segments)

        batches.append(
            {
                "batch_id": f"{chapter_id}-b{chapter_batch_order:03d}",
                "chapter_id": chapter_id,
                "order": global_batch_order,
                "chapter_order": chapter_batch_order,
                "segment_ids": segment_ids,
                "segments": compact_segments,
                "source_text": format_source_text(compact_segments),
                "char_count": char_count,
                "source_byte_count": sum(
                    byte_count(segment["source"]) for segment in compact_segments
                ),
                "status": "pending",
            }
        )
        current_segments = []
        current_source_bytes = 0
        current_source_chars = 0

    for segment in segments:
        chapter_id = str(segment["chapter_id"])
        source = str(segment["source"])
        source_bytes = byte_count(source)
        source_chars = len(source)

        if current_chapter_id is None:
            current_chapter_id = chapter_id
        elif chapter_id != current_chapter_id:
            flush()
            current_chapter_id = chapter_id
            chapter_batch_order = 0

        would_exceed_bytes = current_source_bytes + source_bytes > max_bytes
        would_exceed_chars = (
            max_chars is not None
            and current_source_chars + source_chars > max_chars
        )
        would_exceed_segments = len(current_segments) >= max_segments

        if current_segments and (
            would_exceed_bytes or would_exceed_chars or would_exceed_segments
        ):
            flush()

        current_segments.append(segment)
        current_source_bytes += source_bytes
        current_source_chars += source_chars

    flush()
    return batches


def validate_batches(
    segments: list[dict[str, Any]],
    batches: list[dict[str, Any]],
) -> None:
    validate_batch_rows(batches)
    expected_ids = [str(segment["id"]) for segment in segments]
    actual_ids = [
        str(segment_id)
        for batch in batches
        for segment_id in batch["segment_ids"]
    ]

    if actual_ids != expected_ids:
        missing_ids = sorted(set(expected_ids) - set(actual_ids))
        extra_ids = sorted(set(actual_ids) - set(expected_ids))
        raise ValueError(
            "batch coverage does not match segments; "
            f"missing={missing_ids[:10]}, extra={extra_ids[:10]}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Group stable source segments into chapter-local LLM batches."
    )
    parser.add_argument(
        "--input",
        default=SEGMENTS_PATH,
        type=Path,
        help="Segments JSONL file. Default: work/segments.jsonl",
    )
    parser.add_argument(
        "--output",
        default=BATCHES_PATH,
        type=Path,
        help="Batches JSONL output file. Default: work/batches.jsonl",
    )
    parser.add_argument(
        "--max-bytes",
        default=10000,
        type=int,
        help="Maximum UTF-8 source bytes per batch. Default: 10000",
    )
    parser.add_argument(
        "--max-segments",
        default=24,
        type=int,
        help="Maximum segments per batch. Default: 24",
    )
    parser.add_argument(
        "--max-chars",
        default=None,
        type=int,
        help="Optional legacy maximum source characters per batch. Default: disabled",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    segments = read_jsonl(args.input)
    validate_segments(segments)

    batches = build_batches(
        segments,
        max_bytes=args.max_bytes,
        max_segments=args.max_segments,
        max_chars=args.max_chars,
    )
    validate_batches(segments, batches)
    write_jsonl(args.output, batches)

    emit_json_report(
        {
            "input": str(args.input),
            "output": str(args.output),
            "segments": len(segments),
            "batches": len(batches),
            "max_bytes": args.max_bytes,
            "max_chars": args.max_chars,
            "max_segments": args.max_segments,
            "largest_batch_bytes": max(
                (batch["source_byte_count"] for batch in batches),
                default=0,
            ),
            "largest_batch_chars": max(
                (batch["char_count"] for batch in batches),
                default=0,
            ),
            "largest_batch_segments": max(
                (len(batch["segment_ids"]) for batch in batches),
                default=0,
            ),
        }
    )


if __name__ == "__main__":
    main()
