"""Formatting and validation helpers for inline EPUB image placeholders."""

from __future__ import annotations

import re
from urllib.parse import quote, unquote


EPUB_IMAGE_PLACEHOLDER_PREFIX = "⟦EPUB_IMG:"
EPUB_IMAGE_PLACEHOLDER_RE = re.compile(
    r"⟦EPUB_IMG:(?P<src>[^|⟧]*)\|alt=(?P<alt>[^⟧]*)⟧"
)


def format_epub_image_placeholder(src: str, alt: str) -> str:
    """Encode an inline EPUB image as a stable translation token."""

    escaped_src = quote(src, safe="/._-")
    escaped_alt = quote(alt, safe="._-")
    return f"{EPUB_IMAGE_PLACEHOLDER_PREFIX}{escaped_src}|alt={escaped_alt}⟧"


def extract_epub_image_placeholders(text: str) -> list[str]:
    """Return complete placeholders in source order."""

    return [match.group(0) for match in EPUB_IMAGE_PLACEHOLDER_RE.finditer(text)]


def replace_epub_image_placeholders_for_display(text: str) -> str:
    """Render non-empty alt text and hide images without alt text."""

    return EPUB_IMAGE_PLACEHOLDER_RE.sub(
        lambda match: unquote(match.group("alt")),
        text,
    )
