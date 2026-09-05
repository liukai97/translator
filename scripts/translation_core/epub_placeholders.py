"""Formatting and validation helpers for inline EPUB image placeholders."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, unquote


EPUB_IMAGE_PLACEHOLDER_PREFIX = "⟦EPUB_IMG:"
EPUB_IMAGE_PLACEHOLDER_RE = re.compile(
    r"⟦EPUB_IMG:(?P<src>[^|⟧]*)\|alt=(?P<alt>[^⟧]*)⟧"
)


@dataclass(frozen=True)
class EpubImagePlaceholder:
    """One decoded inline-image placeholder."""

    token: str
    src: str
    alt: str


def format_epub_image_placeholder(src: str, alt: str) -> str:
    """Encode an inline EPUB image as a stable translation token."""

    escaped_src = quote(src, safe="/._-")
    escaped_alt = quote(alt, safe="._-")
    return f"{EPUB_IMAGE_PLACEHOLDER_PREFIX}{escaped_src}|alt={escaped_alt}⟧"


def parse_epub_image_placeholder(token: str) -> EpubImagePlaceholder:
    """Decode one complete placeholder or reject malformed/non-canonical input."""

    match = EPUB_IMAGE_PLACEHOLDER_RE.fullmatch(token)
    if match is None:
        raise ValueError(f"invalid EPUB image placeholder: {token!r}")
    try:
        src = unquote(match.group("src"), encoding="utf-8", errors="strict")
        alt = unquote(match.group("alt"), encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError(f"invalid UTF-8 in EPUB image placeholder: {token!r}") from exc
    if format_epub_image_placeholder(src, alt) != token:
        raise ValueError(f"non-canonical EPUB image placeholder: {token!r}")
    return EpubImagePlaceholder(token=token, src=src, alt=alt)


def extract_epub_image_placeholders(text: str) -> list[str]:
    """Return complete placeholders in source order."""

    return [match.group(0) for match in EPUB_IMAGE_PLACEHOLDER_RE.finditer(text)]


def tokenize_epub_text(text: str) -> list[str | EpubImagePlaceholder]:
    """Split translated text into ordinary text and decoded image tokens."""

    tokens: list[str | EpubImagePlaceholder] = []
    position = 0
    for match in EPUB_IMAGE_PLACEHOLDER_RE.finditer(text):
        plain_text = text[position : match.start()]
        if EPUB_IMAGE_PLACEHOLDER_PREFIX in plain_text:
            raise ValueError("malformed EPUB image placeholder in translated text")
        if plain_text:
            tokens.append(plain_text)
        tokens.append(parse_epub_image_placeholder(match.group(0)))
        position = match.end()

    remainder = text[position:]
    if EPUB_IMAGE_PLACEHOLDER_PREFIX in remainder:
        raise ValueError("malformed EPUB image placeholder in translated text")
    if remainder:
        tokens.append(remainder)
    return tokens


def replace_epub_image_placeholders_for_display(text: str) -> str:
    """Render non-empty alt text and hide images without alt text."""

    return EPUB_IMAGE_PLACEHOLDER_RE.sub(
        lambda match: unquote(match.group("alt")),
        text,
    )
