"""Remove standalone page markers from a source Markdown/text file.

The source book may contain many lines like ``[Page 1]`` or ``[page 2]``.
Those markers are useful for scraped pagination, but they are too fine-grained
for chapter-based translation work.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PAGE_MARKER_RE = re.compile(r"^\s*\[page\s+\d+\]\s*$", re.IGNORECASE)


def read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


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
        default="input/book.md",
        type=Path,
        help="Source text/Markdown file. Default: input/book.md",
    )
    parser.add_argument(
        "--output",
        default="work/book.no_pages.md",
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

    print(
        json.dumps(
            {
                "input": str(args.input),
                "output": str(args.output),
                "removed_page_markers": removed,
                "written_lines": len(cleaned_text.splitlines()),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
