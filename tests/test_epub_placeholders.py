from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from translation_core.epub_placeholders import (  # noqa: E402
    EpubImagePlaceholder,
    extract_epub_image_placeholders,
    format_epub_image_placeholder,
    parse_epub_image_placeholder,
    tokenize_epub_text,
)


class EpubPlaceholderTests(unittest.TestCase):
    def test_round_trips_percent_encoded_src_and_alt(self) -> None:
        token = format_epub_image_placeholder(
            "../图片/a b|c.png?x=1",
            "异体字 | 20%",
        )

        parsed = parse_epub_image_placeholder(token)

        self.assertEqual(parsed.src, "../图片/a b|c.png?x=1")
        self.assertEqual(parsed.alt, "异体字 | 20%")
        self.assertNotIn("图片", token)
        self.assertNotIn(" ", token)

    def test_supports_empty_alt_and_repeated_tokens_in_order(self) -> None:
        token = format_epub_image_placeholder("../images/same.png", "")
        text = f"前{token}中{token}后"

        parts = tokenize_epub_text(text)

        self.assertEqual(extract_epub_image_placeholders(text), [token, token])
        self.assertEqual(
            parts,
            [
                "前",
                EpubImagePlaceholder(token, "../images/same.png", ""),
                "中",
                EpubImagePlaceholder(token, "../images/same.png", ""),
                "后",
            ],
        )

    def test_rejects_changed_missing_and_malformed_tokens(self) -> None:
        token = format_epub_image_placeholder("image.png", "原文")
        changed = token.replace("%E5%8E%9F%E6%96%87", "%E8%AF%91%E6%96%87")

        self.assertNotEqual(
            extract_epub_image_placeholders(token),
            extract_epub_image_placeholders(changed),
        )
        self.assertEqual(extract_epub_image_placeholders("plain text"), [])
        with self.assertRaisesRegex(ValueError, "malformed"):
            tokenize_epub_text("前⟦EPUB_IMG:image.png|alt=broken")
        with self.assertRaisesRegex(ValueError, "non-canonical"):
            parse_epub_image_placeholder("⟦EPUB_IMG:image%2fpart.png|alt=⟧")


if __name__ == "__main__":
    unittest.main()
