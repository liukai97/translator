from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from epub_to_markdown import convert_epub, local_name  # noqa: E402
from segment import build_segments  # noqa: E402
from translation_core.epub_build import (  # noqa: E402
    EpubBuildError,
    build_translated_epub,
)
from translation_core.epub_placeholders import (  # noqa: E402
    extract_epub_image_placeholders,
)
from translation_core.jsonl import write_jsonl  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_epub3(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "mimetype",
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr(
            "META-INF/container.xml",
            """<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="EPUB/book.opf"/></rootfiles></container>""",
        )
        archive.writestr(
            "EPUB/book.opf",
            """<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="old-id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:title>原书名</dc:title><dc:language>ja</dc:language>
  <dc:identifier id="old-id">urn:uuid:old-value</dc:identifier>
  <meta name="primary-writing-mode" content="vertical-rl"/>
</metadata>
<manifest>
  <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
  <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  <item id="css" href="style.css" media-type="text/css"/>
  <item id="heading" href="images/heading.png" media-type="image/png"/>
  <item id="inline" href="images/inline.png" media-type="image/png"/>
</manifest><spine page-progression-direction="rtl"><itemref idref="chapter"/></spine></package>""",
        )
        archive.writestr(
            "EPUB/nav.xhtml",
            """<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
 lang="ja" xml:lang="ja"><head><title>Nav</title></head><body>
<nav epub:type="toc"><ol><li><a href="chapter.xhtml#start">第一章</a></li></ol></nav>
</body></html>""",
        )
        archive.writestr(
            "EPUB/chapter.xhtml",
            """<html xmlns="http://www.w3.org/1999/xhtml" class="vrtl" xml:lang="ja"><head>
<title>第一章</title><link href="style.css" rel="stylesheet" type="text/css"/></head><body>
<p id="start"><img src="images/heading.png" alt="第一章" class="heading"/></p>
<p class="body">前<ruby><rb>漢</rb><rt>かん</rt></ruby><br class="old"/>后<img
 src="images/inline.png" alt="符" class="gaiji" width="12" data-extra="keep"/>末</p>
<p><a href="#start" class="internal">链接原文</a></p>
<p><a href="https://example.com" class="external">https://example.com</a>（原日期）</p>
</body></html>""",
        )
        archive.writestr("EPUB/style.css", "html.vrtl { writing-mode: vertical-rl; }")
        archive.writestr("EPUB/images/heading.png", b"heading-image")
        archive.writestr("EPUB/images/inline.png", b"inline-image")


def write_epub2(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED
        )
        archive.writestr(
            "META-INF/container.xml",
            """<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="old">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>旧书</dc:title>
<dc:language>ja</dc:language><dc:identifier id="old">old</dc:identifier></metadata>
<manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>
<spine toc="ncx"><itemref idref="chapter"/></spine></package>""",
        )
        archive.writestr(
            "OEBPS/toc.ncx",
            """<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap><navPoint>
<navLabel><text>旧章</text></navLabel><content src="chapter.xhtml#start"/>
</navPoint></navMap></ncx>""",
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            """<html xmlns="http://www.w3.org/1999/xhtml" class="vrtl"><head><title>旧章</title></head>
<body><p id="start">旧章</p><p>旧正文。</p></body></html>""",
        )


def write_translation_inputs(
    epub_path: Path,
    directory: Path,
    body_translations: list[str],
    *,
    title: str,
    heading: str,
) -> tuple[Path, Path, Path]:
    canonical_path = directory / "book.md"
    segments_path = directory / "segments.jsonl"
    translations_path = directory / "translations.jsonl"
    canonical = convert_epub(epub_path).markdown
    canonical_path.write_text(canonical, encoding="utf-8", newline="\n")
    segments = build_segments(canonical, max_segment_bytes=100_000)
    write_jsonl(segments_path, segments)
    translations = []
    for segment in segments:
        if segment["kind"] == "book_title":
            translation = f"# {title}"
        elif segment["kind"] == "heading":
            translation = f"## {heading}"
        else:
            source_parts = str(segment["source"]).split("\n\n")
            self_parts = body_translations[: len(source_parts)]
            if len(self_parts) != len(source_parts):
                raise AssertionError("test translation block count mismatch")
            translation = "\n\n".join(self_parts)
            del body_translations[: len(source_parts)]
        translations.append(
            {
                "segment_id": segment["id"],
                "translation": translation,
                "status": "translated",
            }
        )
    if body_translations:
        raise AssertionError("unused test translations")
    write_jsonl(translations_path, translations)
    return canonical_path, segments_path, translations_path


class BuildTranslatedEpubTests(unittest.TestCase):
    def test_dry_run_then_builds_epub3_and_preserves_resources_and_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source_epub = directory / "source.epub"
            output_epub = directory / "translated.epub"
            write_epub3(source_epub)
            token = extract_epub_image_placeholders(convert_epub(source_epub).markdown)[0]
            canonical, segments, translations = write_translation_inputs(
                source_epub,
                directory,
                [
                    f"译文前\n译文后{token}末",
                    "中文链接",
                    "https://example.com（译日期）",
                ],
                title="中文书名",
                heading="中文第一章",
            )
            original_hash = digest(source_epub)

            dry_report = build_translated_epub(
                source_epub,
                source_path=canonical,
                segments_path=segments,
                translations_path=translations,
                output_path=output_epub,
                dry_run=True,
            )
            self.assertEqual(dry_report["status"], "dry_run")
            self.assertEqual(dry_report["body_blocks"], 3)
            self.assertFalse(output_epub.exists())

            with patch("translation_core.epub_build.shutil.which", return_value=None):
                report = build_translated_epub(
                    source_epub,
                    source_path=canonical,
                    segments_path=segments,
                    translations_path=translations,
                    output_path=output_epub,
                )

            self.assertEqual(digest(source_epub), original_hash)
            self.assertEqual(report["restored_images"], 1)
            self.assertEqual(report["readback_placeholders"], 1)
            self.assertEqual(report["epubcheck"], "not_available")
            expected = (
                f"# 中文书名\n\n## 中文第一章\n\n译文前\n"
                f"译文后{token}末\n\n中文链接\n\nhttps://example.com（译日期）\n"
            )
            self.assertEqual(convert_epub(output_epub).markdown, expected)

            with zipfile.ZipFile(source_epub) as original, zipfile.ZipFile(output_epub) as output:
                self.assertEqual(output.infolist()[0].filename, "mimetype")
                self.assertEqual(output.infolist()[0].compress_type, zipfile.ZIP_STORED)
                original_css = original.read("EPUB/style.css")
                translated_css = output.read("EPUB/style.css")
                self.assertNotEqual(original_css, translated_css)
                self.assertNotIn(b"vertical-rl", translated_css)
                self.assertIn(b"horizontal-tb", translated_css)
                self.assertIn(b"force horizontal layout", translated_css)
                self.assertEqual(
                    original.read("EPUB/images/inline.png"),
                    output.read("EPUB/images/inline.png"),
                )
                chapter = ET.fromstring(output.read("EPUB/chapter.xhtml"))
                html_classes = chapter.attrib.get("class", "").split()
                self.assertIn("hltr", html_classes)
                self.assertNotIn("vrtl", html_classes)
                self.assertEqual(chapter.attrib.get("dir"), "ltr")
                self.assertIn("horizontal-tb", chapter.attrib.get("style", ""))
                body = next(node for node in chapter.iter() if local_name(node.tag) == "body")
                self.assertEqual(body.attrib.get("dir"), "ltr")
                self.assertIn("horizontal-tb", body.attrib.get("style", ""))
                style = next(
                    node
                    for node in chapter.iter()
                    if local_name(node.tag) == "style"
                    and node.attrib.get("data-translated-epub") == "horizontal"
                )
                self.assertIn("horizontal-tb", style.text or "")
                images = [node for node in chapter.iter() if local_name(node.tag) == "img"]
                self.assertEqual(images[0].attrib["alt"], "中文第一章")
                self.assertEqual(
                    images[1].attrib,
                    {
                        "src": "images/inline.png",
                        "alt": "符",
                        "class": "gaiji",
                        "width": "12",
                        "data-extra": "keep",
                    },
                )
                links = [node for node in chapter.iter() if local_name(node.tag) == "a"]
                self.assertEqual(links[0].attrib, {"href": "#start", "class": "internal"})
                self.assertEqual("".join(links[0].itertext()), "中文链接")
                self.assertEqual(links[1].attrib["href"], "https://example.com")

                nav = ET.fromstring(output.read("EPUB/nav.xhtml"))
                nav_link = next(node for node in nav.iter() if local_name(node.tag) == "a")
                self.assertEqual(nav_link.attrib["href"], "chapter.xhtml#start")
                self.assertEqual("".join(nav_link.itertext()), "中文第一章")
                package = ET.fromstring(output.read("EPUB/book.opf"))
                spine = next(
                    node for node in package.iter() if local_name(node.tag) == "spine"
                )
                self.assertEqual(spine.attrib.get("page-progression-direction"), "ltr")
                values = {
                    local_name(node.tag): (node.text or "")
                    for node in package.iter()
                    if local_name(node.tag) in {"title", "language"}
                }
                self.assertEqual(values["title"], "中文书名")
                self.assertEqual(values["language"], "zh-CN")
                identifiers = [
                    node.text or ""
                    for node in package.iter()
                    if local_name(node.tag) == "identifier"
                ]
                self.assertIn("urn:uuid:old-value", identifiers)
                self.assertTrue(any(value.startswith("urn:uuid:") for value in identifiers[1:]))

            with self.assertRaisesRegex(EpubBuildError, "output already exists"):
                build_translated_epub(
                    source_epub,
                    source_path=canonical,
                    segments_path=segments,
                    translations_path=translations,
                    output_path=output_epub,
                )

    def test_updates_epub2_ncx_and_visible_heading(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source_epub = directory / "source2.epub"
            output_epub = directory / "translated2.epub"
            write_epub2(source_epub)
            canonical, segments, translations = write_translation_inputs(
                source_epub,
                directory,
                ["新正文。"],
                title="新书",
                heading="新章",
            )

            with patch("translation_core.epub_build.shutil.which", return_value=None):
                build_translated_epub(
                    source_epub,
                    source_path=canonical,
                    segments_path=segments,
                    translations_path=translations,
                    output_path=output_epub,
                )

            self.assertEqual(
                convert_epub(output_epub).markdown,
                "# 新书\n\n## 新章\n\n新正文。\n",
            )
            with zipfile.ZipFile(output_epub) as archive:
                ncx = ET.fromstring(archive.read("OEBPS/toc.ncx"))
                ncx_text = next(node for node in ncx.iter() if local_name(node.tag) == "text")
                self.assertEqual(ncx_text.text, "新章")
                chapter = ET.fromstring(archive.read("OEBPS/chapter.xhtml"))
                heading = next(
                    node for node in chapter.iter() if node.attrib.get("id") == "start"
                )
                self.assertEqual("".join(heading.itertext()), "新章")


if __name__ == "__main__":
    unittest.main()
