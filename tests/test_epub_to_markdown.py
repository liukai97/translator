from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from epub_to_markdown import convert_epub  # noqa: E402


class EpubToMarkdownTests(unittest.TestCase):
    def test_uses_spine_order_flattens_ruby_and_omits_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = Path(temp_dir) / "sample.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr(
                    "META-INF/container.xml",
                    """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/book.opf"/></rootfiles>
</container>""",
                )
                archive.writestr(
                    "EPUB/book.opf",
                    """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>测试书名</dc:title>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="first" href="first.xhtml" media-type="application/xhtml+xml"/>
    <item id="sample" href="p-009.xhtml" media-type="application/xhtml+xml"/>
    <item id="last" href="last.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="last"/><itemref idref="sample"/><itemref idref="first"/></spine>
</package>""",
                )
                archive.writestr(
                    "EPUB/nav.xhtml",
                    """<html xmlns="http://www.w3.org/1999/xhtml"
 xmlns:epub="http://www.idpf.org/2007/ops"><body>
<nav epub:type="toc"><ol>
  <li><a href="last.xhtml#last">末章</a></li>
  <li><a href="p-009.xhtml#sample">普通章节</a></li>
  <li><a href="first.xhtml#first">首章</a></li>
</ol></nav></body></html>""",
                )
                archive.writestr(
                    "EPUB/last.xhtml",
                    """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p id="last"><img src="heading.jpg" alt="末章"/></p>
<p>前<ruby>漢<rt>かん</rt>字<rt>じ</rt></ruby>後</p>
<p>空白 <span>保留</span> 成功<br/>次行</p>
<p><img src="illustration.jpg" alt=""/></p>
</body></html>""",
                )
                archive.writestr(
                    "EPUB/p-009.xhtml",
                    """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p id="sample">普通章节</p><p>不能按文件名特殊排除。</p>
</body></html>""",
                )
                archive.writestr(
                    "EPUB/first.xhtml",
                    """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p id="first"><img src="first.jpg" alt="首章"/></p><p>第一段。</p>
</body></html>""",
                )

            result = convert_epub(epub_path)

        self.assertTrue(result.markdown.startswith("# 测试书名\n"))
        self.assertLess(result.markdown.index("## 末章"), result.markdown.index("## 普通章节"))
        self.assertLess(result.markdown.index("## 普通章节"), result.markdown.index("## 首章"))
        self.assertIn("前漢字後", result.markdown)
        self.assertNotIn("かん", result.markdown)
        self.assertIn("空白 保留 成功\n次行", result.markdown)
        self.assertNotIn("illustration.jpg", result.markdown)
        self.assertNotIn("heading.jpg", result.markdown)
        self.assertIn("不能按文件名特殊排除。", result.markdown)

    def test_uses_epub2_ncx_navigation_when_epub3_nav_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = Path(temp_dir) / "epub2.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr(
                    "META-INF/container.xml",
                    """<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>""",
                )
                archive.writestr(
                    "OEBPS/content.opf",
                    """<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>EPUB 2</dc:title></metadata>
<manifest>
  <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
</manifest>
<spine toc="ncx"><itemref idref="chapter"/></spine></package>""",
                )
                archive.writestr(
                    "OEBPS/toc.ncx",
                    """<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>
<navPoint><navLabel><text>NCX 章节</text></navLabel>
<content src="chapter.xhtml#start"/></navPoint>
</navMap></ncx>""",
                )
                archive.writestr(
                    "OEBPS/chapter.xhtml",
                    """<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p id="start">NCX 章节</p><p>正文。</p>
</body></html>""",
                )

            result = convert_epub(epub_path)

        self.assertIn("## NCX 章节", result.markdown)
        self.assertIn("正文。", result.markdown)


if __name__ == "__main__":
    unittest.main()
