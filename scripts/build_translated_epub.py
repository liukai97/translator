"""Build a translated EPUB while keeping the source EPUB read-only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from translation_core.epub_build import EpubBuildError, build_translated_epub
from translation_core.paths import CLEAN_BOOK_PATH, SEGMENTS_PATH, TRANSLATIONS_PATH


def default_output_path(input_path: Path) -> Path:
    return Path("output") / f"{input_path.stem}.zh-CN.epub"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild an EPUB from canonical translated segments. The source EPUB "
            "is opened read-only and all mappings are validated before writeback."
        )
    )
    parser.add_argument("input", type=Path, help="Original source EPUB")
    parser.add_argument(
        "--source",
        type=Path,
        default=CLEAN_BOOK_PATH,
        help="Canonical Markdown source. Default: work/book.no_pages.md",
    )
    parser.add_argument(
        "--segments",
        type=Path,
        default=SEGMENTS_PATH,
        help="Canonical source segments. Default: work/segments.jsonl",
    )
    parser.add_argument(
        "--translations",
        type=Path,
        default=TRANSLATIONS_PATH,
        help="Completed translations. Default: work/translations.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Translated EPUB path. Default: output/<input>.zh-CN.epub",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run all input and mapping checks without writing an EPUB",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing output EPUB after successful validation",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = args.output or default_output_path(args.input)
    try:
        report = build_translated_epub(
            args.input,
            source_path=args.source,
            segments_path=args.segments,
            translations_path=args.translations,
            output_path=output,
            dry_run=args.dry_run,
            force=args.force,
        )
    except (EpubBuildError, OSError, ValueError) as exc:
        error = exc.to_dict() if isinstance(exc, EpubBuildError) else {
            "stage": "build",
            "message": str(exc),
        }
        print(
            json.dumps({"status": "error", "error": error}, ensure_ascii=False),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
