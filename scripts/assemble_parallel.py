"""Generate a readable source/translation parallel HTML file.

The script joins:

- ``work/segments.jsonl`` as the canonical source/alignment order
- ``work/translations.jsonl`` as translated Chinese entries
- optionally ``work/batches.jsonl`` for batch metadata

It is intentionally tolerant of partial translation progress: untranslated
segments remain visible in the output and are styled as pending.
"""

from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


def read_jsonl(path: Path, required: bool = True) -> list[dict[str, Any]]:
    if not path.exists():
        if required:
            raise FileNotFoundError(path)
        return []

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    return rows


def validate_segments(segments: list[dict[str, Any]]) -> None:
    required_keys = {"id", "chapter_id", "order", "kind", "source"}
    ids: list[str] = []
    for index, segment in enumerate(segments, start=1):
        missing = sorted(required_keys - set(segment))
        if missing:
            raise ValueError(f"segment row {index} missing keys: {', '.join(missing)}")
        ids.append(str(segment["id"]))

    duplicate_ids = sorted({segment_id for segment_id in ids if ids.count(segment_id) > 1})
    if duplicate_ids:
        raise ValueError(f"duplicate segment ids: {', '.join(duplicate_ids[:10])}")


def build_translation_map(
    translations: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    translation_map: dict[str, dict[str, Any]] = {}
    duplicate_ids: list[str] = []

    for row_number, row in enumerate(translations, start=1):
        if "segment_id" not in row:
            raise ValueError(f"translation row {row_number} missing segment_id")
        if "translation" not in row:
            raise ValueError(f"translation row {row_number} missing translation")

        segment_id = str(row["segment_id"])
        if segment_id in translation_map:
            duplicate_ids.append(segment_id)
        translation_map[segment_id] = row

    return translation_map, duplicate_ids


def build_batch_map(batches: list[dict[str, Any]]) -> dict[str, str]:
    batch_by_segment: dict[str, str] = {}
    for row_number, batch in enumerate(batches, start=1):
        if "batch_id" not in batch or "segment_ids" not in batch:
            raise ValueError(f"batch row {row_number} missing batch_id or segment_ids")
        for segment_id in batch["segment_ids"]:
            batch_by_segment[str(segment_id)] = str(batch["batch_id"])
    return batch_by_segment


def line_html(text: str) -> str:
    escaped = html.escape(text)
    return escaped.replace("\n", "<br>\n")


def strip_markdown_heading(text: str) -> str:
    stripped = text.strip()
    while stripped.startswith("#"):
        stripped = stripped[1:].lstrip()
    return stripped


def status_for_translation(row: dict[str, Any] | None) -> str:
    if row is None:
        return "pending"
    translation = str(row.get("translation", "")).strip()
    if not translation:
        return "empty"
    return str(row.get("status") or "translated")


def build_rows(
    segments: list[dict[str, Any]],
    translation_map: dict[str, dict[str, Any]],
    batch_by_segment: dict[str, str],
    only_translated: bool,
    show_metadata: bool,
) -> tuple[str, Counter[str]]:
    parts: list[str] = []
    status_counts: Counter[str] = Counter()

    for segment in segments:
        segment_id = str(segment["id"])
        kind = str(segment["kind"])
        chapter_id = str(segment["chapter_id"])
        source = str(segment["source"])
        translation_row = translation_map.get(segment_id)
        status = status_for_translation(translation_row)
        translation = (
            str(translation_row.get("translation", ""))
            if translation_row is not None
            else ""
        )

        if only_translated and status == "pending":
            continue

        status_counts[status] += 1
        batch_id = batch_by_segment.get(segment_id, "")
        row_classes = ["segment", f"kind-{html.escape(kind)}", f"status-{html.escape(status)}"]
        if kind in {"book_title", "heading"}:
            row_classes.append("is-heading")

        clean_source = strip_markdown_heading(source) if kind in {"book_title", "heading"} else source
        clean_translation = (
            strip_markdown_heading(translation)
            if kind in {"book_title", "heading"}
            else translation
        )

        if status == "pending":
            rendered_translation = '<span class="placeholder">未翻译</span>'
        elif status == "empty":
            rendered_translation = '<span class="placeholder">译文为空</span>'
        else:
            rendered_translation = line_html(clean_translation)

        meta_html = ""
        if show_metadata:
            meta_html = "\n".join(
                [
                    '  <div class="meta">',
                    f'    <a class="segment-id" href="#{html.escape(segment_id)}">{html.escape(segment_id)}</a>',
                    f'    <span>{html.escape(kind)}</span>',
                    f'    <span>{html.escape(chapter_id)}</span>',
                    f'    <span>{html.escape(batch_id) if batch_id else "no-batch"}</span>',
                    f'    <span class="status">{html.escape(status)}</span>',
                    "  </div>",
                ]
            )

        article_attrs = " ".join(
            [
                f'class="{" ".join(row_classes)}"',
                f'id="{html.escape(segment_id)}"',
                f'data-kind="{html.escape(kind)}"',
                f'data-chapter="{html.escape(chapter_id)}"',
                f'data-batch="{html.escape(batch_id)}"',
                f'data-status="{html.escape(status)}"',
            ]
        )

        parts.append(
            "\n".join(
                line
                for line in [
                    f"<article {article_attrs}>",
                    meta_html,
                    '  <div class="parallel">',
                    f'    <section class="source"><p>{line_html(clean_source)}</p></section>',
                    f'    <section class="translation"><p>{rendered_translation}</p></section>',
                    "  </div>",
                    "</article>",
                ]
                if line
            )
        )

    return "\n".join(parts), status_counts


def build_toc(segments: list[dict[str, Any]], translation_map: dict[str, dict[str, Any]]) -> str:
    items: list[str] = []
    for segment in segments:
        kind = str(segment["kind"])
        if kind not in {"book_title", "heading"}:
            continue
        segment_id = str(segment["id"])
        source = strip_markdown_heading(str(segment["source"]))
        translation_row = translation_map.get(segment_id)
        translation = (
            strip_markdown_heading(str(translation_row["translation"]))
            if translation_row and translation_row.get("translation")
            else ""
        )
        label = translation or source
        items.append(
            f'<a href="#{html.escape(segment_id)}">'
            f'<span>{html.escape(segment_id)}</span>{html.escape(label)}</a>'
        )
    return "\n".join(items)


def render_html(
    segments: list[dict[str, Any]],
    translations: list[dict[str, Any]],
    batches: list[dict[str, Any]],
    title: str,
    only_translated: bool,
    show_metadata: bool,
) -> tuple[str, dict[str, Any]]:
    validate_segments(segments)
    translation_map, duplicate_translation_ids = build_translation_map(translations)
    source_ids = {str(segment["id"]) for segment in segments}
    unmatched_translation_ids = sorted(set(translation_map) - source_ids)
    batch_by_segment = build_batch_map(batches)

    rows_html, status_counts = build_rows(
        segments,
        translation_map,
        batch_by_segment,
        only_translated=only_translated,
        show_metadata=show_metadata,
    )
    toc_html = build_toc(segments, translation_map)

    translated_count = sum(
        1
        for segment in segments
        if status_for_translation(translation_map.get(str(segment["id"]))) == "translated"
    )
    pending_count = sum(
        1
        for segment in segments
        if str(segment["id"]) not in translation_map
    )
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    stats = {
        "segments": len(segments),
        "translations": len(translations),
        "translated_segments": translated_count,
        "pending_segments": pending_count,
        "duplicate_translation_ids": duplicate_translation_ids,
        "unmatched_translation_ids": unmatched_translation_ids,
        "rendered_status_counts": dict(status_counts),
    }

    css = """
:root {
  color-scheme: light dark;
  --bg: #f7f4ef;
  --panel: #fffdf8;
  --text: #1f2428;
  --muted: #687076;
  --line: #ded6c9;
  --source: #223047;
  --translation: #231f20;
  --accent: #83623f;
  --pending: #8a6f35;
  --pending-bg: #fff4cb;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: "Microsoft YaHei", "Noto Sans CJK SC", "PingFang SC", system-ui, sans-serif;
  line-height: 1.72;
}
header {
  padding: 28px 32px 22px;
  border-bottom: 1px solid var(--line);
  background: var(--panel);
  position: sticky;
  top: 0;
  z-index: 2;
}
h1 {
  margin: 0 0 12px;
  font-size: 24px;
  font-weight: 700;
  letter-spacing: 0;
}
.summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  color: var(--muted);
  font-size: 13px;
}
.summary span {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 3px 8px;
  background: color-mix(in srgb, var(--panel) 88%, var(--bg));
}
main {
  display: grid;
  grid-template-columns: minmax(180px, 260px) minmax(0, 1fr);
  gap: 0;
}
nav {
  position: sticky;
  top: 111px;
  align-self: start;
  max-height: calc(100vh - 111px);
  overflow: auto;
  padding: 16px;
  border-right: 1px solid var(--line);
}
nav a {
  display: block;
  color: var(--text);
  text-decoration: none;
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 13px;
  line-height: 1.45;
}
nav a:hover { background: var(--panel); }
nav span {
  display: block;
  color: var(--muted);
  font-size: 11px;
}
.content { padding: 16px 24px 40px; }
.segment {
  border-bottom: 1px solid var(--line);
  padding: 14px 0;
  scroll-margin-top: 126px;
}
.segment.is-heading {
  padding-top: 26px;
}
.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
  color: var(--muted);
  font-size: 12px;
}
.meta span,
.meta a {
  color: var(--muted);
  text-decoration: none;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 1px 6px;
  background: color-mix(in srgb, var(--panel) 80%, transparent);
}
.meta .status { color: var(--accent); }
.parallel {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 18px;
}
.source,
.translation {
  min-width: 0;
}
.source p,
.translation p {
  margin: 0;
  white-space: normal;
  overflow-wrap: anywhere;
}
.source {
  color: var(--source);
  font-family: "Yu Gothic", "Meiryo", "Noto Sans CJK JP", "Microsoft YaHei", sans-serif;
}
.translation {
  color: var(--translation);
  font-family: "Microsoft YaHei", "Noto Sans CJK SC", "PingFang SC", sans-serif;
}
.is-heading .source,
.is-heading .translation {
  font-size: 18px;
  font-weight: 700;
}
.placeholder {
  display: inline-block;
  color: var(--pending);
  background: var(--pending-bg);
  border: 1px solid color-mix(in srgb, var(--pending) 35%, transparent);
  border-radius: 6px;
  padding: 1px 7px;
}
.status-pending .translation p,
.status-empty .translation p {
  color: var(--pending);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #151719;
    --panel: #1d2024;
    --text: #e8e3da;
    --muted: #a7a09a;
    --line: #34383e;
    --source: #d9e7ff;
    --translation: #f0e8df;
    --accent: #ddb77d;
    --pending: #f1d185;
    --pending-bg: #3a3018;
  }
}
@media (max-width: 860px) {
  header { position: static; padding: 20px 18px; }
  main { display: block; }
  nav {
    position: static;
    max-height: 220px;
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }
  .content { padding: 12px 16px 32px; }
  .parallel { grid-template-columns: 1fr; gap: 8px; }
  .source { border-bottom: 1px dashed var(--line); padding-bottom: 8px; }
}
"""

    doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>{css}</style>
</head>
<body>
  <header>
    <h1>{html.escape(title)}</h1>
    <div class="summary">
      <span>generated: {html.escape(generated_at)}</span>
      <span>segments: {len(segments)}</span>
      <span>translations: {len(translations)}</span>
      <span>translated: {translated_count}</span>
      <span>pending: {pending_count}</span>
      <span>duplicates: {len(duplicate_translation_ids)}</span>
      <span>unmatched: {len(unmatched_translation_ids)}</span>
    </div>
  </header>
  <main>
    <nav aria-label="Chapters">
      {toc_html}
    </nav>
    <section class="content">
      {rows_html}
    </section>
  </main>
</body>
</html>
"""
    return doc, stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate output/parallel.html from source segments and translations."
    )
    parser.add_argument(
        "--segments",
        default="work/segments.jsonl",
        type=Path,
        help="Source segments JSONL. Default: work/segments.jsonl",
    )
    parser.add_argument(
        "--translations",
        default="work/translations.jsonl",
        type=Path,
        help="Translations JSONL. Default: work/translations.jsonl",
    )
    parser.add_argument(
        "--batches",
        default="work/batches.jsonl",
        type=Path,
        help="Optional batches JSONL for metadata. Default: work/batches.jsonl",
    )
    parser.add_argument(
        "--output",
        default="output/parallel.html",
        type=Path,
        help="Output HTML path. Default: output/parallel.html",
    )
    parser.add_argument(
        "--title",
        default="原文 / 翻译左右对照",
        help="HTML document title.",
    )
    parser.add_argument(
        "--only-translated",
        action="store_true",
        help="Render only segments that already have a translation.",
    )
    parser.add_argument(
        "--show-metadata",
        action="store_true",
        help="Show per-segment id/kind/chapter/batch/status labels for debugging.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    segments = read_jsonl(args.segments)
    translations = read_jsonl(args.translations)
    batches = read_jsonl(args.batches, required=False)

    document, stats = render_html(
        segments,
        translations,
        batches,
        title=args.title,
        only_translated=args.only_translated,
        show_metadata=args.show_metadata,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(document, encoding="utf-8", newline="\n")

    print(
        json.dumps(
            {"output": str(args.output), **stats},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
