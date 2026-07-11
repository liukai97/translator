"""Generate work/segments.jsonl from a Markdown/text book source.

Body segments are chapter-local line blocks capped by UTF-8 byte size by
default. Standalone page markers such as ``[Page 1]`` are ignored by default
and never become segments.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


PAGE_MARKER_RE = re.compile(r"^\s*\[page\s+\d+\]\s*$", re.IGNORECASE)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def normalize_source(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def make_segment_id(chapter_id: str, order: int) -> str:
    return f"{chapter_id}-p{order:04d}"


def byte_count(text: str) -> int:
    return len(text.encode("utf-8"))


def trim_outer_blank_lines(lines: list[str]) -> list[str]:
    start = 0
    end = len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def build_segments(
    text: str,
    strip_page_markers: bool = True,
    max_segment_bytes: int = 500,
) -> list[dict[str, Any]]:
    if max_segment_bytes < 1:
        raise ValueError("max_segment_bytes must be positive")

    segments: list[dict[str, Any]] = []
    chapter_number = 0
    chapter_id = "front"
    order = 0
    body_lines: list[str] = []

    def append_segment(kind: str, source: str) -> None:
        nonlocal order
        normalized_source = source.strip()
        if not normalized_source:
            return
        order += 1
        segments.append(
            {
                "id": make_segment_id(chapter_id, order),
                "chapter_id": chapter_id,
                "order": order,
                "kind": kind,
                "source": normalized_source,
                "status": "imported",
            }
        )

    def segment_text(lines: list[str]) -> str:
        return "\n".join(trim_outer_blank_lines(lines))

    def would_exceed_max(lines: list[str], next_line: str) -> bool:
        if not lines:
            return False
        return byte_count(segment_text([*lines, next_line])) > max_segment_bytes

    def flush_body() -> None:
        nonlocal body_lines
        source = segment_text(body_lines)
        if source:
            append_segment("paragraph", source)
        body_lines = []

    for raw_line in normalize_source(text):
        line = raw_line.rstrip()

        if strip_page_markers and PAGE_MARKER_RE.match(line):
            continue

        heading_match = HEADING_RE.match(line)
        if heading_match:
            flush_body()
            level = len(heading_match.group(1))
            if level == 1 and chapter_number == 0 and chapter_id == "front":
                append_segment("book_title", line)
                continue
            if level == 2:
                chapter_number += 1
                chapter_id = f"ch{chapter_number:03d}"
                order = 0
            append_segment("heading", line)
            continue

        if would_exceed_max(body_lines, line):
            flush_body()
        body_lines.append(line)

    flush_body()
    return segments


def validate_segments(segments: list[dict[str, Any]]) -> None:
    ids = [segment["id"] for segment in segments]
    duplicate_ids = sorted({segment_id for segment_id in ids if ids.count(segment_id) > 1})
    if duplicate_ids:
        raise ValueError(f"duplicate segment ids: {', '.join(duplicate_ids[:10])}")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def default_input_path() -> Path:
    cleaned = Path("work/book.no_pages.md")
    if cleaned.exists():
        return cleaned
    return Path("input/book.md")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split a book source into stable chapter/paragraph segments."
    )
    parser.add_argument(
        "--input",
        default=None,
        type=Path,
        help="Source text/Markdown file. Default: work/book.no_pages.md if present, else input/book.md",
    )
    parser.add_argument(
        "--output",
        default="work/segments.jsonl",
        type=Path,
        help="JSONL output file. Default: work/segments.jsonl",
    )
    parser.add_argument(
        "--keep-page-markers",
        action="store_true",
        help="Keep [Page N] marker lines as normal text instead of ignoring them.",
    )
    parser.add_argument(
        "--max-segment-bytes",
        default=500,
        type=int,
        help=(
            "Maximum UTF-8 bytes per body segment. Segments keep whole lines, "
            "so a single long line may exceed this value. Default: 500"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = args.input or default_input_path()

    source_text = read_text(input_path)
    segments = build_segments(
        source_text,
        strip_page_markers=not args.keep_page_markers,
        max_segment_bytes=args.max_segment_bytes,
    )
    validate_segments(segments)
    write_jsonl(args.output, segments)

    chapter_count = len(
        {segment["chapter_id"] for segment in segments if segment["chapter_id"] != "front"}
    )
    heading_count = sum(1 for segment in segments if segment["kind"] == "heading")
    paragraph_count = sum(1 for segment in segments if segment["kind"] == "paragraph")
    print(
        json.dumps(
            {
                "input": str(input_path),
                "output": str(args.output),
                "segments": len(segments),
                "chapters": chapter_count,
                "headings": heading_count,
                "paragraphs": paragraph_count,
                "max_segment_bytes": args.max_segment_bytes,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
