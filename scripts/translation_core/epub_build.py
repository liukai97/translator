"""Deterministically rebuild an EPUB from canonical translated segments."""

from __future__ import annotations

import copy
import hashlib
import posixpath
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from epub_to_markdown import (
    DC_NS,
    EPUB_NS,
    EpubConversionError,
    ManifestItem,
    block_elements,
    book_title,
    convert_epub,
    element_ids,
    find_first,
    image_alt_titles,
    local_name,
    normalize_text,
    parse_xml,
    read_member,
    read_package,
    render_inline,
    resolve_member,
)
from strip_page_markers import strip_page_markers
from translation_core.epub_placeholders import (
    EpubImagePlaceholder,
    extract_epub_image_placeholders,
    format_epub_image_placeholder,
    tokenize_epub_text,
)
from translation_core.jsonl import read_jsonl
from translation_core.translations import normalize_translation_row
from translation_core.validation import validate_segments, validate_translations


XHTML_NS = "http://www.w3.org/1999/xhtml"
OPF_NS = "http://www.idpf.org/2007/opf"
DCTERMS_NS = "http://purl.org/dc/terms/"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NCX_MEDIA_TYPE = "application/x-dtbncx+xml"
DOCUMENT_MEDIA_TYPES = frozenset({"application/xhtml+xml", "text/html"})
HEADING_RE = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.*)$", re.DOTALL)
HORIZONTAL_STYLE = """
html, body, body * {
  -epub-writing-mode: horizontal-tb !important;
  -webkit-writing-mode: horizontal-tb !important;
  writing-mode: horizontal-tb !important;
  -epub-text-orientation: mixed !important;
  -webkit-text-orientation: mixed !important;
  text-orientation: mixed !important;
}
html, body {
  direction: ltr !important;
}
""".strip()
HORIZONTAL_INLINE_STYLE = (
    "-epub-writing-mode: horizontal-tb !important; "
    "-webkit-writing-mode: horizontal-tb !important; "
    "writing-mode: horizontal-tb !important; "
    "direction: ltr !important;"
)
HORIZONTAL_CSS_MARKER = "/* translated EPUB: force horizontal layout */"
FORBIDDEN_INTERACTIVE_TAGS = frozenset(
    {
        "audio",
        "button",
        "canvas",
        "embed",
        "form",
        "iframe",
        "input",
        "math",
        "object",
        "script",
        "select",
        "svg",
        "textarea",
        "video",
    }
)


class EpubBuildError(RuntimeError):
    """A location-aware deterministic build failure."""

    def __init__(
        self,
        stage: str,
        message: str,
        *,
        member: str | None = None,
        block_index: int | None = None,
        segment_id: str | None = None,
        expected: str | None = None,
        actual: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.message = message
        self.member = member
        self.block_index = block_index
        self.segment_id = segment_id
        self.expected = expected
        self.actual = actual

    def __str__(self) -> str:
        locations = []
        if self.member:
            locations.append(self.member)
        if self.block_index is not None:
            locations.append(f"block {self.block_index}")
        if self.segment_id:
            locations.append(self.segment_id)
        suffix = f" ({', '.join(locations)})" if locations else ""
        return f"{self.stage}: {self.message}{suffix}"

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "stage": self.stage,
            "message": self.message,
        }
        for key in ("member", "block_index", "segment_id", "expected", "actual"):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        return result


@dataclass
class NavigationEntry:
    target_path: str
    fragment: str
    label: str
    member: str
    label_element: ET.Element


@dataclass
class HeadingTarget:
    kind: str
    member: str
    element: ET.Element


@dataclass
class EpubRecord:
    kind: str
    markdown: str
    member: str | None = None
    element: ET.Element | None = None
    heading_targets: list[HeadingTarget] = field(default_factory=list)


@dataclass(frozen=True)
class InputBlock:
    kind: str
    segment_id: str
    source: str
    translation: str


@dataclass
class AlignedBlock:
    record: EpubRecord
    input: InputBlock
    block_index: int

    @property
    def translated_markdown(self) -> str:
        if self.record.kind == "body":
            return self.input.translation
        marks = HEADING_RE.match(self.record.markdown)
        prefix = marks.group("marks") if marks else ("#" if self.record.kind == "book_title" else "##")
        return f"{prefix} {strip_heading_prefix(self.input.translation)}"


@dataclass
class EpubWorkspace:
    opf_path: str
    package: ET.Element
    manifest: dict[str, ManifestItem]
    reading_order: list[str]
    document_order: list[str]
    roots: dict[str, ET.Element]
    navigation_entries: list[NavigationEntry]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def summarize(value: str, limit: int = 120) -> str:
    compact = value.replace("\r", "\\r").replace("\n", "\\n")
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def strip_heading_prefix(text: str) -> str:
    match = HEADING_RE.match(text.strip())
    return (match.group("title") if match else text.strip()).strip()


def edge_equal(expected: str, actual: str) -> bool:
    """Allow only the Unicode edge trimming introduced by segment.py."""

    return expected.strip() == actual.strip()


def restore_dom_edge_whitespace(record: EpubRecord, input_block: InputBlock) -> str:
    """Restore source indentation stripped only at a segment boundary."""

    translated = input_block.translation
    source = input_block.source
    dom_source = record.markdown

    source_has_leading = bool(source) and source[0].isspace()
    translated_has_leading = bool(translated) and translated[0].isspace()
    if not source_has_leading and not translated_has_leading:
        prefix_length = len(dom_source) - len(dom_source.lstrip())
        if prefix_length:
            translated = dom_source[:prefix_length] + translated

    source_has_trailing = bool(source) and source[-1].isspace()
    translated_has_trailing = bool(translated) and translated[-1].isspace()
    if not source_has_trailing and not translated_has_trailing:
        suffix_length = len(dom_source) - len(dom_source.rstrip())
        if suffix_length:
            translated += dom_source[len(dom_source) - suffix_length :]
    return translated


def _qname(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _namespace_of(element: ET.Element) -> str:
    tag = element.tag
    if isinstance(tag, str) and tag.startswith("{"):
        return tag[1:].split("}", 1)[0]
    return ""


def _qualified_like(element: ET.Element, name: str) -> str:
    namespace = _namespace_of(element)
    return _qname(namespace, name) if namespace else name


def _load_epub3_navigation(
    archive: zipfile.ZipFile,
    opf_path: str,
    manifest: dict[str, ManifestItem],
) -> tuple[list[NavigationEntry], dict[str, ET.Element]]:
    nav_item = next((item for item in manifest.values() if "nav" in item.properties), None)
    if nav_item is None:
        return [], {}
    nav_path = resolve_member(opf_path, nav_item.href)
    root = parse_xml(read_member(archive, nav_path), nav_path)
    toc = next(
        (
            element
            for element in root.iter()
            if local_name(element.tag) == "nav"
            and "toc" in element.attrib.get(_qname(EPUB_NS, "type"), "").split()
        ),
        None,
    )
    if toc is None:
        return [], {nav_path: root}

    entries: list[NavigationEntry] = []
    seen: set[tuple[str, str, str]] = set()
    for link in toc.iter():
        if local_name(link.tag) != "a":
            continue
        href = link.attrib.get("href", "")
        label = normalize_text(render_inline(link))
        if not href or not label:
            continue
        key = (
            resolve_member(nav_path, href),
            unquote(urlsplit(href).fragment),
            label,
        )
        if key in seen:
            continue
        seen.add(key)
        entries.append(NavigationEntry(*key, member=nav_path, label_element=link))
    return entries, {nav_path: root}


def _load_epub2_navigation(
    archive: zipfile.ZipFile,
    opf_path: str,
    package: ET.Element,
    manifest: dict[str, ManifestItem],
) -> tuple[list[NavigationEntry], dict[str, ET.Element]]:
    spine = find_first(package, "spine")
    toc_id = spine.attrib.get("toc", "") if spine is not None else ""
    ncx_item = manifest.get(toc_id) or next(
        (item for item in manifest.values() if item.media_type == NCX_MEDIA_TYPE),
        None,
    )
    if ncx_item is None:
        return [], {}
    ncx_path = resolve_member(opf_path, ncx_item.href)
    root = parse_xml(read_member(archive, ncx_path), ncx_path)
    entries: list[NavigationEntry] = []
    seen: set[tuple[str, str, str]] = set()
    for nav_point in root.iter():
        if local_name(nav_point.tag) != "navPoint":
            continue
        content = next(
            (element for element in nav_point.iter() if local_name(element.tag) == "content"),
            None,
        )
        label_element = next(
            (element for element in nav_point.iter() if local_name(element.tag) == "text"),
            None,
        )
        if content is None or label_element is None:
            continue
        href = content.attrib.get("src", "")
        label = normalize_text(render_inline(label_element))
        if not href or not label:
            continue
        key = (
            resolve_member(ncx_path, href),
            unquote(urlsplit(href).fragment),
            label,
        )
        if key in seen:
            continue
        seen.add(key)
        entries.append(NavigationEntry(*key, member=ncx_path, label_element=label_element))
    return entries, {ncx_path: root}


def load_workspace(archive: zipfile.ZipFile) -> EpubWorkspace:
    names = [info.filename for info in archive.infolist()]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise EpubBuildError(
            "input",
            "EPUB contains duplicate ZIP member names",
            actual=", ".join(duplicates[:10]),
        )
    for name in names:
        normalized = posixpath.normpath(name.replace("\\", "/"))
        if normalized == ".." or normalized.startswith("../") or normalized.startswith("/"):
            raise EpubBuildError("input", "unsafe ZIP member path", member=name)
    try:
        mimetype = archive.read("mimetype")
    except KeyError as exc:
        raise EpubBuildError("input", "EPUB has no mimetype member") from exc
    if mimetype != b"application/epub+zip":
        raise EpubBuildError(
            "input",
            "invalid EPUB mimetype content",
            expected="application/epub+zip",
            actual=summarize(mimetype.decode("ascii", errors="replace")),
        )
    corrupt = archive.testzip()
    if corrupt is not None:
        raise EpubBuildError("input", "ZIP CRC validation failed", member=corrupt)

    try:
        opf_path, package, manifest, reading_order = read_package(archive)
    except Exception as exc:
        if isinstance(exc, EpubBuildError):
            raise
        raise EpubBuildError("input", str(exc)) from exc

    roots: dict[str, ET.Element] = {}
    document_order: list[str] = []
    for item_id in reading_order:
        item = manifest.get(item_id)
        if item is None:
            raise EpubBuildError(
                "input", "spine references an unknown manifest item", actual=item_id
            )
        if item.media_type not in DOCUMENT_MEDIA_TYPES:
            continue
        member = resolve_member(opf_path, item.href)
        roots[member] = parse_xml(read_member(archive, member), member)
        document_order.append(member)

    entries, navigation_roots = _load_epub3_navigation(archive, opf_path, manifest)
    if not entries:
        entries, navigation_roots = _load_epub2_navigation(
            archive, opf_path, package, manifest
        )
    roots.update(navigation_roots)
    return EpubWorkspace(
        opf_path=opf_path,
        package=package,
        manifest=manifest,
        reading_order=reading_order,
        document_order=document_order,
        roots=roots,
        navigation_entries=entries,
    )


def _dedupe_targets(targets: list[HeadingTarget]) -> list[HeadingTarget]:
    result: list[HeadingTarget] = []
    seen: set[tuple[str, int]] = set()
    for target in targets:
        key = (target.kind, id(target.element))
        if key not in seen:
            seen.add(key)
            result.append(target)
    return result


def _append_heading_record(
    records: list[EpubRecord],
    title: str,
    level: int,
    member: str,
    targets: list[HeadingTarget],
) -> None:
    markdown = f"{'#' * level} {title}"
    if records and records[-1].markdown == markdown:
        records[-1].heading_targets = _dedupe_targets(
            [*records[-1].heading_targets, *targets]
        )
        return
    records.append(
        EpubRecord(
            kind="heading",
            markdown=markdown,
            member=member,
            heading_targets=_dedupe_targets(targets),
        )
    )


def extract_records(workspace: EpubWorkspace, fallback_title: str) -> list[EpubRecord]:
    records = [
        EpubRecord(
            kind="book_title",
            markdown=f"# {book_title(workspace.package, fallback_title)}",
            member=workspace.opf_path,
        )
    ]
    entries_by_path: dict[str, list[NavigationEntry]] = {}
    for entry in workspace.navigation_entries:
        entries_by_path.setdefault(entry.target_path, []).append(entry)

    for member in workspace.document_order:
        root = workspace.roots[member]
        body = find_first(root, "body")
        if body is None:
            continue
        elements = block_elements(body)
        if not elements:
            fallback = render_inline(body)
            if fallback:
                records.append(
                    EpubRecord(kind="body", markdown=fallback, member=member, element=body)
                )
            continue

        local_records: list[EpubRecord] = []
        entries = entries_by_path.get(member, [])
        document_entries = [entry for entry in entries if not entry.fragment]
        document_titles = [entry.label for entry in document_entries]
        has_content = any(
            render_inline(element) or image_alt_titles(element) for element in elements
        )
        if has_content:
            for entry in document_entries:
                _append_heading_record(
                    local_records,
                    entry.label,
                    2,
                    member,
                    [HeadingTarget("navigation", entry.member, entry.label_element)],
                )

        for element in elements:
            ids = element_ids(element)
            fragment_entries = [
                entry for entry in entries if entry.fragment and entry.fragment in ids
            ]
            text_without_images = render_inline(element)
            alt_titles = image_alt_titles(element) if not text_without_images else []
            tag = local_name(element.tag)
            text = render_inline(
                element,
                include_image_placeholders=bool(text_without_images),
            )

            title_order: list[str] = []
            title_levels: dict[str, int] = {}
            title_targets: dict[str, list[HeadingTarget]] = {}

            def add_title(title: str, level: int, targets: list[HeadingTarget]) -> None:
                if title not in title_levels:
                    title_order.append(title)
                    title_levels[title] = level
                title_targets.setdefault(title, []).extend(targets)

            for entry in fragment_entries:
                targets = [HeadingTarget("navigation", entry.member, entry.label_element)]
                if text_without_images == entry.label:
                    targets.append(HeadingTarget("element", member, element))
                add_title(entry.label, 2, targets)
            for title in alt_titles:
                images = [
                    candidate
                    for candidate in element.iter()
                    if local_name(candidate.tag) == "img"
                    and normalize_text(candidate.attrib.get("alt", "")) == title
                ]
                add_title(
                    title,
                    2,
                    [HeadingTarget("image_alt", member, image) for image in images],
                )
            if re.fullmatch(r"h[1-6]", tag) and text:
                add_title(
                    text,
                    min(int(tag[1]) + 1, 6),
                    [HeadingTarget("element", member, element)],
                )

            for title in title_order:
                _append_heading_record(
                    local_records,
                    title,
                    title_levels[title],
                    member,
                    title_targets[title],
                )

            if text_without_images in document_titles:
                for record in reversed(local_records):
                    if strip_heading_prefix(record.markdown) == text_without_images:
                        record.heading_targets = _dedupe_targets(
                            [
                                *record.heading_targets,
                                HeadingTarget("element", member, element),
                            ]
                        )
                        break

            title_texts = set(title_order)
            if text and not (
                re.fullmatch(r"h[1-6]", tag)
                or text in title_texts
                or text in document_titles
            ):
                local_records.append(
                    EpubRecord(
                        kind="body",
                        markdown=text,
                        member=member,
                        element=element,
                    )
                )
        records.extend(local_records)
    return records


def load_input_blocks(
    segments_path: Path,
    translations_path: Path,
) -> tuple[list[InputBlock], int, int, int]:
    try:
        segments = read_jsonl(segments_path)
        translations = read_jsonl(translations_path)
        segment_ids = validate_segments(segments)
        translation_ids = validate_translations(
            translations,
            known_segment_ids=set(segment_ids),
        )
    except (OSError, ValueError) as exc:
        raise EpubBuildError("translations", str(exc)) from exc

    if set(translation_ids) != set(segment_ids):
        missing = [segment_id for segment_id in segment_ids if segment_id not in set(translation_ids)]
        raise EpubBuildError(
            "translations",
            "translation IDs do not exactly cover source segments",
            actual=", ".join(missing[:20]),
        )
    translation_by_id = {str(row["segment_id"]): row for row in translations}

    blocks: list[InputBlock] = []
    placeholder_count = 0
    heading_count = 0
    for row_number, segment in enumerate(segments, start=1):
        segment_id = str(segment["id"])
        source = str(segment["source"])
        kind = str(segment["kind"])
        try:
            translation = normalize_translation_row(
                translation_by_id[segment_id],
                segment_id=segment_id,
                source=source,
                location=f"{translations_path}:{row_number}",
            )
        except ValueError as exc:
            raise EpubBuildError(
                "translations", str(exc), segment_id=segment_id
            ) from exc
        if translation["status"] != "translated":
            raise EpubBuildError(
                "translations",
                "release builds require status=translated",
                segment_id=segment_id,
                actual=translation["status"],
            )
        translated_text = translation["translation"]
        source_blank_lines = [
            index for index, line in enumerate(source.split("\n")) if not line.strip()
        ]
        translation_blank_lines = [
            index
            for index, line in enumerate(translated_text.split("\n"))
            if not line.strip()
        ]
        if source_blank_lines != translation_blank_lines:
            raise EpubBuildError(
                "translations",
                "source/translation blank-line positions differ",
                segment_id=segment_id,
                expected=str(source_blank_lines[:20]),
                actual=str(translation_blank_lines[:20]),
            )

        if kind == "paragraph":
            source_parts = source.split("\n\n")
            translation_parts = translated_text.split("\n\n")
        elif kind in {"book_title", "heading"}:
            source_parts = [source]
            translation_parts = [translated_text]
            if kind == "heading":
                heading_count += 1
        else:
            raise EpubBuildError(
                "segments",
                "unsupported segment kind",
                segment_id=segment_id,
                actual=kind,
            )
        if len(source_parts) != len(translation_parts):
            raise EpubBuildError(
                "translations",
                "source/translation block counts differ",
                segment_id=segment_id,
                expected=str(len(source_parts)),
                actual=str(len(translation_parts)),
            )
        for source_part, translated_part in zip(source_parts, translation_parts):
            placeholder_count += len(extract_epub_image_placeholders(source_part))
            blocks.append(
                InputBlock(
                    kind="body" if kind == "paragraph" else kind,
                    segment_id=segment_id,
                    source=source_part,
                    translation=translated_part,
                )
            )
    return blocks, len(segments), heading_count, placeholder_count


def align_records(records: list[EpubRecord], inputs: list[InputBlock]) -> list[AlignedBlock]:
    if len(records) != len(inputs):
        raise EpubBuildError(
            "mapping",
            "extracted EPUB block count does not match segmented source",
            expected=str(len(inputs)),
            actual=str(len(records)),
        )
    aligned: list[AlignedBlock] = []
    for index, (record, input_block) in enumerate(zip(records, inputs), start=1):
        if record.kind != input_block.kind:
            raise EpubBuildError(
                "mapping",
                "block kind mismatch",
                member=record.member,
                block_index=index,
                segment_id=input_block.segment_id,
                expected=input_block.kind,
                actual=record.kind,
            )
        if not edge_equal(input_block.source, record.markdown):
            raise EpubBuildError(
                "mapping",
                "source block does not match the EPUB DOM in reading order",
                member=record.member,
                block_index=index,
                segment_id=input_block.segment_id,
                expected=summarize(input_block.source),
                actual=summarize(record.markdown),
            )
        aligned.append(AlignedBlock(record=record, input=input_block, block_index=index))
    return aligned


def _all_image_nodes(element: ET.Element) -> list[ET.Element]:
    return [candidate for candidate in element.iter() if local_name(candidate.tag) == "img"]


def _mixed_link_parts(
    element: ET.Element,
    translation: str,
) -> tuple[str, ET.Element, str] | None:
    link_nodes = [
        candidate
        for candidate in element.iter()
        if candidate is not element and local_name(candidate.tag) == "a"
    ]
    direct_children = list(element)
    if link_nodes:
        if (
            len(link_nodes) != 1
            or len(direct_children) != 1
            or direct_children[0] is not link_nodes[0]
        ):
            raise ValueError("mixed or nested link content is not safely replaceable")
        link = link_nodes[0]
        if not (element.text or "").strip() and not (link.tail or "").strip():
            return None
        link_text = render_inline(link)
        if not link_text or translation.count(link_text) != 1:
            raise ValueError(
                "mixed link text must remain unchanged and occur once in the translation"
            )
        if _all_image_nodes(element) or extract_epub_image_placeholders(translation):
            raise ValueError("mixed links with inline images are not supported")
        before, after = translation.split(link_text, 1)
        return before, link, after
    return None


def _safe_wrapper(element: ET.Element, translation: str) -> ET.Element | None:
    mixed_link = _mixed_link_parts(element, translation)
    if mixed_link is not None:
        return None
    link_nodes = [
        candidate
        for candidate in element.iter()
        if candidate is not element and local_name(candidate.tag) == "a"
    ]
    direct_children = list(element)
    if link_nodes:
        return link_nodes[0]
    if (
        len(direct_children) == 1
        and local_name(direct_children[0].tag) == "span"
        and not (element.text or "").strip()
        and not (direct_children[0].tail or "").strip()
    ):
        return direct_children[0]
    return None


def validate_replacement_shapes(aligned: list[AlignedBlock]) -> int:
    image_count = 0
    for item in aligned:
        if item.record.kind != "body":
            continue
        element = item.record.element
        if element is None:
            raise EpubBuildError(
                "mapping",
                "mapped body block has no DOM element",
                member=item.record.member,
                block_index=item.block_index,
                segment_id=item.input.segment_id,
            )
        forbidden = sorted(
            {
                local_name(candidate.tag)
                for candidate in element.iter()
                if local_name(candidate.tag) in FORBIDDEN_INTERACTIVE_TAGS
            }
        )
        if forbidden:
            raise EpubBuildError(
                "mapping",
                "unsupported interactive content in translatable block",
                member=item.record.member,
                block_index=item.block_index,
                segment_id=item.input.segment_id,
                actual=", ".join(forbidden),
            )
        try:
            _safe_wrapper(element, item.input.translation)
            tokenize_epub_text(item.input.translation)
        except ValueError as exc:
            raise EpubBuildError(
                "mapping",
                str(exc),
                member=item.record.member,
                block_index=item.block_index,
                segment_id=item.input.segment_id,
            ) from exc

        images = _all_image_nodes(element)
        dom_tokens = [
            format_epub_image_placeholder(
                image.attrib.get("src", ""), image.attrib.get("alt", "")
            )
            for image in images
        ]
        source_tokens = extract_epub_image_placeholders(item.input.source)
        translated_tokens = extract_epub_image_placeholders(item.input.translation)
        if dom_tokens != source_tokens or source_tokens != translated_tokens:
            raise EpubBuildError(
                "mapping",
                "DOM/source/translation image placeholder sequences differ",
                member=item.record.member,
                block_index=item.block_index,
                segment_id=item.input.segment_id,
                expected=str(source_tokens),
                actual=str(dom_tokens),
            )
        image_count += len(images)
    return image_count


def _clear_content(element: ET.Element) -> None:
    element.text = None
    for child in list(element):
        element.remove(child)


def _append_text(container: ET.Element, last: ET.Element | None, text: str) -> None:
    if not text:
        return
    if last is None:
        container.text = (container.text or "") + text
    else:
        last.tail = (last.tail or "") + text


def _append_plain_text(
    container: ET.Element,
    last: ET.Element | None,
    text: str,
) -> ET.Element | None:
    parts = text.split("\n")
    for index, part in enumerate(parts):
        _append_text(container, last, part)
        if index < len(parts) - 1:
            last = ET.SubElement(container, _qualified_like(container, "br"))
    return last


def rewrite_element_content(element: ET.Element, translation: str) -> int:
    images = [copy.deepcopy(image) for image in _all_image_nodes(element)]
    mixed_link = _mixed_link_parts(element, translation)
    if mixed_link is not None:
        before, original_link, after = mixed_link
        link = copy.deepcopy(original_link)
        link.tail = None
        _clear_content(element)
        last = _append_plain_text(element, None, before)
        element.append(link)
        last = link
        _append_plain_text(element, last, after)
        return 0

    wrapper = _safe_wrapper(element, translation)
    if wrapper is None:
        _clear_content(element)
        container = element
    else:
        wrapper.tail = None
        element.text = None
        for child in list(element):
            if child is not wrapper:
                element.remove(child)
        _clear_content(wrapper)
        container = wrapper

    last: ET.Element | None = None
    image_index = 0
    for token in tokenize_epub_text(translation):
        if isinstance(token, EpubImagePlaceholder):
            if image_index >= len(images):
                raise ValueError("translation contains more image tokens than the DOM block")
            image = images[image_index]
            image_index += 1
            image.tail = None
            container.append(image)
            last = image
        else:
            last = _append_plain_text(container, last, token)
    if image_index != len(images):
        raise ValueError("translation did not consume every original image node")
    return image_index


def _update_heading_target(target: HeadingTarget, translation: str) -> None:
    if target.kind == "image_alt":
        target.element.attrib["alt"] = translation
        return
    rewrite_element_content(target.element, translation)


def _metadata_element(package: ET.Element) -> ET.Element:
    metadata = find_first(package, "metadata")
    if metadata is None:
        raise EpubBuildError("metadata", "EPUB package has no metadata element")
    return metadata


def update_package_metadata(package: ET.Element, translated_title: str) -> str:
    metadata = _metadata_element(package)
    title_nodes = list(metadata.iter(_qname(DC_NS, "title")))
    if not title_nodes:
        title_nodes = [ET.SubElement(metadata, _qname(DC_NS, "title"))]
    title_nodes[0].text = translated_title

    language_nodes = list(metadata.iter(_qname(DC_NS, "language")))
    if not language_nodes:
        language_nodes = [ET.SubElement(metadata, _qname(DC_NS, "language"))]
    for language in language_nodes:
        language.text = "zh-CN"

    modified = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    modified_nodes = [
        element
        for element in metadata.iter()
        if local_name(element.tag) == "meta"
        and element.attrib.get("property") == "dcterms:modified"
    ]
    if modified_nodes:
        modified_nodes[0].text = modified
    else:
        node = ET.SubElement(metadata, _qname(OPF_NS, "meta"))
        node.attrib["property"] = "dcterms:modified"
        node.text = modified

    for element in metadata.iter():
        if local_name(element.tag) == "meta" and element.attrib.get("name") == "primary-writing-mode":
            element.attrib["content"] = "horizontal-tb"

    spine = find_first(package, "spine")
    if spine is None:
        raise EpubBuildError("metadata", "EPUB package has no spine element")
    spine.attrib["page-progression-direction"] = "ltr"

    identifier_id = "translated-uuid"
    existing_ids = {element.attrib.get("id", "") for element in package.iter()}
    suffix = 2
    while identifier_id in existing_ids:
        identifier_id = f"translated-uuid-{suffix}"
        suffix += 1
    new_uuid = str(uuid.uuid4())
    identifier = ET.SubElement(metadata, _qname(DC_NS, "identifier"))
    identifier.attrib["id"] = identifier_id
    identifier.text = f"urn:uuid:{new_uuid}"
    package.attrib["unique-identifier"] = identifier_id
    return new_uuid


def make_horizontal(root: ET.Element) -> None:
    html = root if local_name(root.tag) == "html" else find_first(root, "html")
    if html is None:
        return
    classes = html.attrib.get("class", "").split()
    classes = ["hltr" if value == "vrtl" else value for value in classes]
    if classes:
        html.attrib["class"] = " ".join(dict.fromkeys(classes))
    html.attrib["lang"] = "zh-CN"
    html.attrib[_qname(XML_NS, "lang")] = "zh-CN"
    html.attrib["dir"] = "ltr"
    html_style = html.attrib.get("style", "").strip()
    if HORIZONTAL_INLINE_STYLE not in html_style:
        html.attrib["style"] = (
            f"{html_style.rstrip(';')}; {HORIZONTAL_INLINE_STYLE}"
            if html_style
            else HORIZONTAL_INLINE_STYLE
        )
    body = find_first(root, "body")
    if body is not None:
        body.attrib["dir"] = "ltr"
        body_style = body.attrib.get("style", "").strip()
        if HORIZONTAL_INLINE_STYLE not in body_style:
            body.attrib["style"] = (
                f"{body_style.rstrip(';')}; {HORIZONTAL_INLINE_STYLE}"
                if body_style
                else HORIZONTAL_INLINE_STYLE
            )
    head = find_first(root, "head")
    if head is None:
        head = ET.Element(_qualified_like(html, "head"))
        html.insert(0, head)
    existing = next(
        (
            element
            for element in head
            if local_name(element.tag) == "style"
            and element.attrib.get("data-translated-epub") == "horizontal"
        ),
        None,
    )
    if existing is None:
        existing = ET.SubElement(
            head,
            _qualified_like(html, "style"),
            {"type": "text/css", "data-translated-epub": "horizontal"},
        )
    existing.text = HORIZONTAL_STYLE


def apply_translations(
    workspace: EpubWorkspace,
    aligned: list[AlignedBlock],
) -> tuple[set[str], str, int]:
    modified_members = {workspace.opf_path}
    restored_images = 0
    book_title_translation = next(
        strip_heading_prefix(item.input.translation)
        for item in aligned
        if item.record.kind == "book_title"
    )
    new_uuid = update_package_metadata(workspace.package, book_title_translation)

    for item in aligned:
        record = item.record
        visible_translation = strip_heading_prefix(item.input.translation)
        try:
            if record.kind == "body":
                if record.element is None or record.member is None:
                    raise ValueError("mapped body block is missing its DOM location")
                restored_images += rewrite_element_content(
                    record.element,
                    restore_dom_edge_whitespace(record, item.input),
                )
                modified_members.add(record.member)
            elif record.kind == "heading":
                if not record.heading_targets:
                    raise ValueError("heading has no unique navigation or DOM target")
                for target in record.heading_targets:
                    _update_heading_target(target, visible_translation)
                    modified_members.add(target.member)
        except ValueError as exc:
            raise EpubBuildError(
                "writeback",
                str(exc),
                member=record.member,
                block_index=item.block_index,
                segment_id=item.input.segment_id,
            ) from exc

    for member in modified_members:
        root = workspace.roots.get(member)
        if root is not None and local_name(root.tag) == "html":
            make_horizontal(root)
    return modified_members, new_uuid, restored_images


def horizontal_css_replacements(
    archive: zipfile.ZipFile,
    workspace: EpubWorkspace,
) -> dict[str, bytes]:
    """Rewrite linked CSS so readers that discard inline styles stay horizontal."""

    replacements: dict[str, bytes] = {}
    for item in workspace.manifest.values():
        if item.media_type != "text/css":
            continue
        member = resolve_member(workspace.opf_path, item.href)
        data = read_member(archive, member)
        has_bom = data.startswith(b"\xef\xbb\xbf")
        try:
            css = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise EpubBuildError(
                "writeback",
                "CSS resource is not valid UTF-8",
                member=member,
            ) from exc
        css = css.replace("vertical-rl", "horizontal-tb").replace(
            "vertical-lr", "horizontal-tb"
        )
        if HORIZONTAL_CSS_MARKER not in css:
            css = css.rstrip() + f"\n\n{HORIZONTAL_CSS_MARKER}\n{HORIZONTAL_STYLE}\n"
        encoded = css.encode("utf-8")
        replacements[member] = (b"\xef\xbb\xbf" + encoded) if has_bom else encoded
    return replacements


def serialize_xml(root: ET.Element) -> bytes:
    namespace = _namespace_of(root)
    if namespace:
        ET.register_namespace("", namespace)
    ET.register_namespace("epub", EPUB_NS)
    ET.register_namespace("dc", DC_NS)
    ET.register_namespace("dcterms", DCTERMS_NS)
    ET.register_namespace("xlink", XLINK_NS)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def write_archive(
    source: zipfile.ZipFile,
    destination: Path,
    workspace: EpubWorkspace,
    modified_members: set[str],
    byte_replacements: dict[str, bytes] | None = None,
) -> None:
    replacements: dict[str, bytes] = {workspace.opf_path: serialize_xml(workspace.package)}
    replacements.update(byte_replacements or {})
    for member in modified_members:
        if member == workspace.opf_path or member in replacements:
            continue
        root = workspace.roots.get(member)
        if root is None:
            raise EpubBuildError("packaging", "modified XML root is unavailable", member=member)
        replacements[member] = serialize_xml(root)

    infos = source.infolist()
    info_by_name = {info.filename: info for info in infos}
    with zipfile.ZipFile(destination, "w", allowZip64=True) as output:
        output.comment = source.comment
        mimetype_info = copy.copy(info_by_name["mimetype"])
        mimetype_info.compress_type = zipfile.ZIP_STORED
        output.writestr(mimetype_info, b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for info in infos:
            if info.filename == "mimetype":
                continue
            cloned = copy.copy(info)
            data = replacements.get(info.filename, source.read(info.filename))
            output.writestr(cloned, data, compress_type=info.compress_type)


def _manifest_resource_paths(
    opf_path: str,
    manifest: dict[str, ManifestItem],
) -> dict[str, ManifestItem]:
    return {resolve_member(opf_path, item.href): item for item in manifest.values()}


def _verify_local_references(
    archive: zipfile.ZipFile,
    opf_path: str,
    manifest: dict[str, ManifestItem],
) -> int:
    names = set(archive.namelist())
    resources = _manifest_resource_paths(opf_path, manifest)
    for member in resources:
        if member not in names:
            raise EpubBuildError(
                "verification", "manifest resource is missing", member=member
            )

    checked = 0
    parsed_roots: dict[str, ET.Element] = {}
    for member, item in resources.items():
        if item.media_type not in DOCUMENT_MEDIA_TYPES and item.media_type != NCX_MEDIA_TYPE:
            continue
        root = parse_xml(archive.read(member), member)
        parsed_roots[member] = root
        for element in root.iter():
            tag = local_name(element.tag)
            attribute_names = []
            if tag in {"a", "image", "link"}:
                attribute_names.extend(["href", _qname(XLINK_NS, "href")])
            if tag in {"img", "script", "source"}:
                attribute_names.append("src")
            if tag == "content" and item.media_type == NCX_MEDIA_TYPE:
                attribute_names.append("src")
            for attribute in attribute_names:
                href = element.attrib.get(attribute, "").strip()
                if not href:
                    continue
                parsed = urlsplit(href)
                if parsed.scheme or href.startswith("//"):
                    continue
                target = member if not parsed.path else resolve_member(member, href)
                if target not in names:
                    raise EpubBuildError(
                        "verification",
                        "local XHTML reference is missing",
                        member=member,
                        actual=href,
                    )
                fragment = unquote(parsed.fragment)
                if fragment and target in resources and resources[target].media_type in DOCUMENT_MEDIA_TYPES:
                    target_root = parsed_roots.get(target)
                    if target_root is None:
                        target_root = parse_xml(archive.read(target), target)
                        parsed_roots[target] = target_root
                    if not any(node.attrib.get("id") == fragment for node in target_root.iter()):
                        raise EpubBuildError(
                            "verification",
                            "local XHTML fragment target is missing",
                            member=member,
                            actual=href,
                        )
                checked += 1
    return checked


def verify_output(
    original_path: Path,
    output_path: Path,
    aligned: list[AlignedBlock],
    modified_members: set[str],
) -> dict[str, Any]:
    with zipfile.ZipFile(original_path) as original, zipfile.ZipFile(output_path) as output:
        infos = output.infolist()
        if not infos or infos[0].filename != "mimetype":
            raise EpubBuildError("verification", "mimetype is not the first ZIP member")
        if infos[0].compress_type != zipfile.ZIP_STORED:
            raise EpubBuildError("verification", "mimetype ZIP member is compressed")
        if output.testzip() is not None:
            raise EpubBuildError("verification", "output ZIP CRC validation failed")
        if set(original.namelist()) != set(output.namelist()):
            raise EpubBuildError("verification", "output ZIP member set changed")
        for member in original.namelist():
            if member not in modified_members and original.read(member) != output.read(member):
                raise EpubBuildError(
                    "verification", "unmodified ZIP member bytes changed", member=member
                )

        original_opf, _, original_manifest, original_spine = read_package(original)
        output_opf, output_package, output_manifest, output_spine = read_package(output)
        if original_opf != output_opf or original_spine != output_spine:
            raise EpubBuildError("verification", "package path or spine order changed")
        output_spine_node = find_first(output_package, "spine")
        output_direction = (
            output_spine_node.attrib.get("page-progression-direction")
            if output_spine_node is not None
            else None
        )
        if output_direction != "ltr":
            raise EpubBuildError(
                "verification",
                "translated EPUB page progression is not left-to-right",
                expected="ltr",
                actual=str(output_direction),
            )

        original_resources = _manifest_resource_paths(original_opf, original_manifest)
        output_resources = _manifest_resource_paths(output_opf, output_manifest)
        image_hashes = 0
        horizontal_css_files = 0
        for member, item in original_resources.items():
            if item.media_type.startswith("image/"):
                if member not in output_resources or original.read(member) != output.read(member):
                    raise EpubBuildError(
                        "verification", "image resource changed", member=member
                    )
                image_hashes += 1
            elif item.media_type == "text/css":
                if member not in output_resources:
                    raise EpubBuildError(
                        "verification", "CSS resource is missing", member=member
                    )
                css = output.read(member).decode("utf-8-sig")
                if "vertical-rl" in css or "vertical-lr" in css:
                    raise EpubBuildError(
                        "verification",
                        "CSS still contains a vertical writing mode",
                        member=member,
                    )
                if HORIZONTAL_CSS_MARKER not in css or "horizontal-tb" not in css:
                    raise EpubBuildError(
                        "verification",
                        "CSS has no horizontal layout override",
                        member=member,
                    )
                horizontal_css_files += 1

        for member, item in output_resources.items():
            if item.media_type in DOCUMENT_MEDIA_TYPES or item.media_type == NCX_MEDIA_TYPE:
                parse_xml(output.read(member), member)
        checked_references = _verify_local_references(
            output, output_opf, output_manifest
        )

    actual = convert_epub(output_path)
    actual_blocks = actual.markdown.rstrip("\n").split("\n\n")
    expected_blocks = [item.translated_markdown for item in aligned]
    if len(actual_blocks) != len(expected_blocks):
        first_difference = next(
            (
                index
                for index, (expected, observed) in enumerate(
                    zip(expected_blocks, actual_blocks), start=1
                )
                if not edge_equal(expected, observed)
            ),
            min(len(expected_blocks), len(actual_blocks)) + 1,
        )
        expected_value = (
            expected_blocks[first_difference - 1]
            if first_difference <= len(expected_blocks)
            else "<end>"
        )
        actual_value = (
            actual_blocks[first_difference - 1]
            if first_difference <= len(actual_blocks)
            else "<end>"
        )
        raise EpubBuildError(
            "readback",
            "translated EPUB block count changed after writeback",
            block_index=first_difference,
            segment_id=(
                aligned[first_difference - 1].input.segment_id
                if first_difference <= len(aligned)
                else None
            ),
            expected=str(len(expected_blocks)),
            actual=(
                f"{len(actual_blocks)}; first expected={summarize(expected_value)}; "
                f"actual={summarize(actual_value)}"
            ),
        )
    for index, (expected, observed, item) in enumerate(
        zip(expected_blocks, actual_blocks, aligned), start=1
    ):
        if not edge_equal(expected, observed):
            raise EpubBuildError(
                "readback",
                "translated EPUB content differs from expected translation",
                member=item.record.member,
                block_index=index,
                segment_id=item.input.segment_id,
                expected=summarize(expected),
                actual=summarize(observed),
            )
    expected_placeholders = [
        token
        for item in aligned
        for token in extract_epub_image_placeholders(item.input.translation)
    ]
    actual_placeholders = extract_epub_image_placeholders(actual.markdown)
    if expected_placeholders != actual_placeholders:
        raise EpubBuildError(
            "readback",
            "image placeholder sequence changed after writeback",
            expected=str(len(expected_placeholders)),
            actual=str(len(actual_placeholders)),
        )

    epubcheck = shutil.which("epubcheck")
    epubcheck_status = "not_available"
    if epubcheck:
        result = subprocess.run(
            [epubcheck, str(output_path)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        epubcheck_status = "passed" if result.returncode == 0 else "failed"
        if result.returncode != 0:
            raise EpubBuildError(
                "epubcheck",
                "EPUBCheck rejected the generated EPUB",
                actual=summarize(result.stdout + result.stderr, 500),
            )
    return {
        "zip_members": len(infos),
        "verified_resource_hashes": image_hashes,
        "verified_image_hashes": image_hashes,
        "verified_horizontal_css": horizontal_css_files,
        "verified_local_references": checked_references,
        "readback_blocks": len(actual_blocks),
        "readback_placeholders": len(actual_placeholders),
        "epubcheck": epubcheck_status,
    }


def build_translated_epub(
    epub_path: Path,
    *,
    source_path: Path,
    segments_path: Path,
    translations_path: Path,
    output_path: Path,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    epub_path = epub_path.resolve()
    source_path = source_path.resolve()
    segments_path = segments_path.resolve()
    translations_path = translations_path.resolve()
    output_path = output_path.resolve()
    if epub_path == output_path:
        raise EpubBuildError("output", "input and output EPUB paths must differ")
    if not epub_path.is_file():
        raise EpubBuildError("input", "source EPUB does not exist", actual=str(epub_path))
    if not source_path.is_file():
        raise EpubBuildError("source", "canonical source does not exist", actual=str(source_path))
    if output_path.exists() and not force and not dry_run:
        raise EpubBuildError(
            "output",
            "output already exists; use --force to replace it",
            actual=str(output_path),
        )

    input_hash = sha256_file(epub_path)
    try:
        canonical_source = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise EpubBuildError("source", str(exc)) from exc
    try:
        converted = convert_epub(epub_path)
    except (EpubConversionError, OSError) as exc:
        raise EpubBuildError("source", f"cannot re-extract source EPUB: {exc}") from exc
    normalized_markdown, _ = strip_page_markers(converted.markdown)
    if normalized_markdown != canonical_source:
        mismatch = next(
            (
                index
                for index, (expected, actual) in enumerate(
                    zip(canonical_source, normalized_markdown)
                )
                if expected != actual
            ),
            min(len(canonical_source), len(normalized_markdown)),
        )
        raise EpubBuildError(
            "source",
            "EPUB re-extraction differs from canonical source",
            block_index=mismatch,
            expected=summarize(canonical_source[mismatch : mismatch + 100]),
            actual=summarize(normalized_markdown[mismatch : mismatch + 100]),
        )

    input_blocks, segment_count, heading_count, placeholder_count = load_input_blocks(
        segments_path, translations_path
    )
    temporary_path: Path | None = None
    report: dict[str, Any]
    try:
        with zipfile.ZipFile(epub_path) as archive:
            workspace = load_workspace(archive)
            records = extract_records(workspace, epub_path.stem)
            aligned = align_records(records, input_blocks)
            dom_image_count = validate_replacement_shapes(aligned)
            body_count = sum(item.record.kind == "body" for item in aligned)
            mapped_heading_count = sum(item.record.kind == "heading" for item in aligned)
            report = {
                "status": "dry_run" if dry_run else "built",
                "input": str(epub_path),
                "output": str(output_path),
                "input_sha256": input_hash,
                "segments": segment_count,
                "translations": segment_count,
                "mapped_blocks": len(aligned),
                "body_blocks": body_count,
                "headings": mapped_heading_count,
                "source_headings": heading_count,
                "placeholders": placeholder_count,
                "dom_inline_images": dom_image_count,
                "documents_with_content": converted.documents,
            }
            if dry_run:
                if sha256_file(epub_path) != input_hash:
                    raise EpubBuildError("source", "source EPUB changed during dry-run")
                return report

            modified_members, new_uuid, restored_images = apply_translations(
                workspace, aligned
            )
            css_replacements = horizontal_css_replacements(archive, workspace)
            modified_members.update(css_replacements)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                prefix=output_path.name + ".",
                suffix=".tmp",
                dir=output_path.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
            write_archive(
                archive,
                temporary_path,
                workspace,
                modified_members,
                css_replacements,
            )

        verification = verify_output(
            epub_path,
            temporary_path,
            aligned,
            modified_members,
        )
        if sha256_file(epub_path) != input_hash:
            raise EpubBuildError("source", "source EPUB changed during build")
        temporary_path.replace(output_path)
        temporary_path = None
        report.update(
            {
                "output_sha256": sha256_file(output_path),
                "new_uuid": new_uuid,
                "modified_members": sorted(modified_members),
                "restored_images": restored_images,
                **verification,
            }
        )
        return report
    except zipfile.BadZipFile as exc:
        raise EpubBuildError("input", f"cannot read EPUB ZIP: {exc}") from exc
    except EpubConversionError as exc:
        raise EpubBuildError("verification", str(exc)) from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
