"""Remove standalone page markers from a source Markdown/text file.

The source book may contain many lines like ``[Page 1]`` or ``[page 2]``.
Those markers are useful for scraped pagination, but they are too fine-grained
for chapter-based translation work.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from translation_core.cli import emit_json_report
from translation_core.paths import CLEAN_BOOK_PATH, INPUT_BOOK_PATH
from translation_core.text import read_text


PAGE_MARKER_RE = re.compile(r"^\s*\[page\s+\d+\]\s*$", re.IGNORECASE)


def strip_page_markers(text: str) -> tuple[str, int]:
    removed = 0
    kept_lines: list[str] = []

    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if PAGE_MARKER_RE.match(line):
            removed += 1
            continue
        kept_lines.append(line.rstrip())

    return "\n".join(kept_lines).rstrip() + "\n", removed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove standalone [Page N] markers from a book source file."
    )
    parser.add_argument(
        "--input",
        default=INPUT_BOOK_PATH,
        type=Path,
        help="Source text/Markdown file. Default: input/book.md",
    )
    parser.add_argument(
        "--output",
        default=CLEAN_BOOK_PATH,
        type=Path,
        help="Cleaned output file. Default: work/book.no_pages.md",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_text = read_text(args.input)
    cleaned_text, removed = strip_page_markers(source_text)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(cleaned_text, encoding="utf-8", newline="\n")

    emit_json_report(
        {
            "input": str(args.input),
            "output": str(args.output),
            "removed_page_markers": removed,
            "written_lines": len(cleaned_text.splitlines()),
        }
    )


if __name__ == "__main__":
    main()
