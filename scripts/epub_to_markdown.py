"""Convert an EPUB book to translation-friendly Markdown.

The converter follows the reading order declared by the EPUB package rather
than sorting XHTML filenames. Images are not extracted or linked. A non-empty
HTML ``img`` alt attribute is treated as a section title, while Japanese ruby
annotations are flattened to their base text by dropping ``rt``/``rp`` nodes.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET


CONTAINER_PATH = "META-INF/container.xml"
DC_NS = "http://purl.org/dc/elements/1.1/"
EPUB_NS = "http://www.idpf.org/2007/ops"

BLOCK_TAGS = {"p", "li", "dt", "dd", "pre", *(f"h{i}" for i in range(1, 7))}
SKIPPED_INLINE_TAGS = {"rt", "rp", "script", "style"}
ASCII_EDGE_WHITESPACE = " \t\r\n"


class EpubConversionError(RuntimeError):
    """Raised when an EPUB cannot be converted safely."""


@dataclass(frozen=True)
class ManifestItem:
    href: str
    media_type: str
    properties: frozenset[str]


@dataclass(frozen=True)
class ConversionResult:
    markdown: str
    documents: int
    headings: int
    paragraphs: int


def local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def parse_xml(data: bytes, member: str) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise EpubConversionError(f"invalid XML/XHTML in {member}: {exc}") from exc


def read_member(archive: zipfile.ZipFile, member: str) -> bytes:
    try:
        return archive.read(member)
    except KeyError as exc:
        raise EpubConversionError(f"EPUB member not found: {member}") from exc


def resolve_member(base_member: str, href: str) -> str:
    href_path = unquote(urlsplit(href).path)
    member = posixpath.normpath(
        posixpath.join(posixpath.dirname(base_member), href_path)
    )
    if member == ".." or member.startswith("../") or member.startswith("/"):
        raise EpubConversionError(f"EPUB reference leaves the archive: {href}")
    return member


def normalize_text(text: str) -> str:
    """Trim ASCII edge whitespace without stripping Japanese indentation."""

    return text.strip(ASCII_EDGE_WHITESPACE)


def clean_text_node(text: str | None) -> str:
    """Remove source-code line wrapping while retaining explicit HTML breaks."""

    return (text or "").replace("\r", "").replace("\n", "")


def collect_inline(element: ET.Element) -> str:
    parts = [clean_text_node(element.text)]
    for child in element:
        tag = local_name(child.tag)
        if tag in SKIPPED_INLINE_TAGS or tag in {"img", "image", "svg"}:
            pass
        elif tag == "br":
            parts.append("\n")
        else:
            parts.append(collect_inline(child))
        parts.append(clean_text_node(child.tail))
    return "".join(parts)


def render_inline(element: ET.Element) -> str:
    """Render visible inline text, omitting ruby readings and images."""

    return normalize_text(collect_inline(element))


def find_first(root: ET.Element, name: str) -> ET.Element | None:
    return next((element for element in root.iter() if local_name(element.tag) == name), None)


def package_path(archive: zipfile.ZipFile) -> str:
    root = parse_xml(read_member(archive, CONTAINER_PATH), CONTAINER_PATH)
    for element in root.iter():
        if local_name(element.tag) == "rootfile":
            path = element.attrib.get("full-path", "").strip()
            if path:
                return path
    raise EpubConversionError("META-INF/container.xml has no package rootfile")


def read_package(
    archive: zipfile.ZipFile,
) -> tuple[str, ET.Element, dict[str, ManifestItem], list[str]]:
    opf_path = package_path(archive)
    root = parse_xml(read_member(archive, opf_path), opf_path)

    manifest: dict[str, ManifestItem] = {}
    for element in root.iter():
        if local_name(element.tag) != "item":
            continue
        item_id = element.attrib.get("id", "").strip()
        href = element.attrib.get("href", "").strip()
        if not item_id or not href:
            continue
        manifest[item_id] = ManifestItem(
            href=href,
            media_type=element.attrib.get("media-type", "").strip(),
            properties=frozenset(element.attrib.get("properties", "").split()),
        )

    spine = find_first(root, "spine")
    if spine is None:
        raise EpubConversionError("EPUB package has no spine")

    reading_order = [
        element.attrib["idref"]
        for element in spine
        if local_name(element.tag) == "itemref"
        and element.attrib.get("idref")
        and element.attrib.get("linear", "yes").lower() != "no"
    ]
    if not reading_order:
        raise EpubConversionError("EPUB spine has no readable documents")

    return opf_path, root, manifest, reading_order


def book_title(package: ET.Element, fallback: str) -> str:
    for element in package.iter(f"{{{DC_NS}}}title"):
        title = normalize_text("".join(element.itertext()))
        if title:
            return title
    return fallback


def add_navigation_target(
    targets: dict[str, list[tuple[str, str]]],
    base_member: str,
    href: str,
    label: str,
) -> None:
    label = normalize_text(label)
    if not href or not label:
        return
    target_path = resolve_member(base_member, href)
    fragment = unquote(urlsplit(href).fragment)
    target = (fragment, label)
    if target not in targets[target_path]:
        targets[target_path].append(target)


def epub3_navigation_targets(
    archive: zipfile.ZipFile,
    opf_path: str,
    manifest: dict[str, ManifestItem],
) -> dict[str, list[tuple[str, str]]]:
    targets: dict[str, list[tuple[str, str]]] = defaultdict(list)
    nav_item = next((item for item in manifest.values() if "nav" in item.properties), None)
    if nav_item is None:
        return targets

    nav_path = resolve_member(opf_path, nav_item.href)
    root = parse_xml(read_member(archive, nav_path), nav_path)
    toc = next(
        (
            element
            for element in root.iter()
            if local_name(element.tag) == "nav"
            and "toc" in element.attrib.get(f"{{{EPUB_NS}}}type", "").split()
        ),
        None,
    )
    if toc is None:
        return targets

    for link in toc.iter():
        if local_name(link.tag) != "a":
            continue
        add_navigation_target(
            targets,
            nav_path,
            link.attrib.get("href", ""),
            render_inline(link),
        )
    return targets


def epub2_navigation_targets(
    archive: zipfile.ZipFile,
    opf_path: str,
    package: ET.Element,
    manifest: dict[str, ManifestItem],
) -> dict[str, list[tuple[str, str]]]:
    targets: dict[str, list[tuple[str, str]]] = defaultdict(list)
    spine = find_first(package, "spine")
    toc_id = spine.attrib.get("toc", "") if spine is not None else ""
    ncx_item = manifest.get(toc_id) or next(
        (
            item
            for item in manifest.values()
            if item.media_type == "application/x-dtbncx+xml"
        ),
        None,
    )
    if ncx_item is None:
        return targets

    ncx_path = resolve_member(opf_path, ncx_item.href)
    root = parse_xml(read_member(archive, ncx_path), ncx_path)
    for nav_point in root.iter():
        if local_name(nav_point.tag) != "navPoint":
            continue
        content = next(
            (
                element
                for element in nav_point.iter()
                if local_name(element.tag) == "content"
            ),
            None,
        )
        nav_label = next(
            (
                element
                for element in nav_point
                if local_name(element.tag) == "navLabel"
            ),
            None,
        )
        if content is None or nav_label is None:
            continue
        add_navigation_target(
            targets,
            ncx_path,
            content.attrib.get("src", ""),
            render_inline(nav_label),
        )
    return targets


def navigation_targets(
    archive: zipfile.ZipFile,
    opf_path: str,
    package: ET.Element,
    manifest: dict[str, ManifestItem],
) -> dict[str, list[tuple[str, str]]]:
    targets = epub3_navigation_targets(archive, opf_path, manifest)
    if targets:
        return targets
    return epub2_navigation_targets(archive, opf_path, package, manifest)


def block_elements(body: ET.Element) -> list[ET.Element]:
    parent_by_child = {child: parent for parent in body.iter() for child in parent}
    blocks: list[ET.Element] = []

    for element in body.iter():
        if local_name(element.tag) not in BLOCK_TAGS:
            continue
        ancestor = parent_by_child.get(element)
        nested = False
        while ancestor is not None and ancestor is not body:
            if local_name(ancestor.tag) in BLOCK_TAGS:
                nested = True
                break
            ancestor = parent_by_child.get(ancestor)
        if not nested:
            blocks.append(element)

    return blocks


def element_ids(element: ET.Element) -> set[str]:
    return {
        candidate.attrib["id"]
        for candidate in element.iter()
        if candidate.attrib.get("id")
    }


def image_alt_titles(element: ET.Element) -> list[str]:
    titles: list[str] = []
    for candidate in element.iter():
        if local_name(candidate.tag) != "img":
            continue
        title = normalize_text(candidate.attrib.get("alt", ""))
        if title and title not in titles:
            titles.append(title)
    return titles


def append_heading(blocks: list[str], title: str, level: int = 2) -> None:
    heading = f"{'#' * level} {title}"
    if not blocks or blocks[-1] != heading:
        blocks.append(heading)


def convert_document(
    root: ET.Element,
    targets: list[tuple[str, str]],
) -> tuple[list[str], int, int]:
    body = find_first(root, "body")
    if body is None:
        return [], 0, 0

    elements = block_elements(body)
    if not elements:
        fallback = render_inline(body)
        return ([fallback] if fallback else []), 0, int(bool(fallback))

    blocks: list[str] = []
    heading_count = 0
    paragraph_count = 0
    document_titles = [label for fragment, label in targets if not fragment]

    if document_titles:
        # Add document-level navigation titles only when there is visible content.
        has_content = any(render_inline(element) or image_alt_titles(element) for element in elements)
        if has_content:
            for title in document_titles:
                before = len(blocks)
                append_heading(blocks, title)
                if len(blocks) > before:
                    heading_count += 1

    for element in elements:
        ids = element_ids(element)
        nav_titles = [
            label for fragment, label in targets if fragment and fragment in ids
        ]
        alt_titles = image_alt_titles(element)
        tag = local_name(element.tag)
        text = render_inline(element)

        titles: list[tuple[str, int]] = []
        for title in [*nav_titles, *alt_titles]:
            if title not in {candidate for candidate, _ in titles}:
                titles.append((title, 2))
        if re.fullmatch(r"h[1-6]", tag) and text:
            level = min(int(tag[1]) + 1, 6)
            if text not in {candidate for candidate, _ in titles}:
                titles.append((text, level))

        for title, level in titles:
            before = len(blocks)
            append_heading(blocks, title, level)
            if len(blocks) > before:
                heading_count += 1

        title_texts = {title for title, _ in titles}
        if text and not (
            re.fullmatch(r"h[1-6]", tag)
            or text in title_texts
            or text in document_titles
        ):
            blocks.append(text)
            paragraph_count += 1

    return blocks, heading_count, paragraph_count


def convert_epub(epub_path: Path) -> ConversionResult:
    try:
        archive = zipfile.ZipFile(epub_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise EpubConversionError(f"cannot open EPUB {epub_path}: {exc}") from exc

    with archive:
        opf_path, package, manifest, reading_order = read_package(archive)
        title = book_title(package, epub_path.stem)
        targets = navigation_targets(archive, opf_path, package, manifest)

        markdown_blocks = [f"# {title}"]
        documents = 0
        headings = 1
        paragraphs = 0

        for item_id in reading_order:
            item = manifest.get(item_id)
            if item is None:
                raise EpubConversionError(f"spine references unknown manifest id: {item_id}")
            if item.media_type not in {"application/xhtml+xml", "text/html"}:
                continue

            document_path = resolve_member(opf_path, item.href)
            root = parse_xml(read_member(archive, document_path), document_path)
            blocks, document_headings, document_paragraphs = convert_document(
                root,
                targets.get(document_path, []),
            )
            if not blocks:
                continue
            markdown_blocks.extend(blocks)
            documents += 1
            headings += document_headings
            paragraphs += document_paragraphs

    markdown = "\n\n".join(markdown_blocks).rstrip() + "\n"
    return ConversionResult(
        markdown=markdown,
        documents=documents,
        headings=headings,
        paragraphs=paragraphs,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert EPUB XHTML in spine order to Markdown. Images are omitted, "
            "image alt text becomes headings, and ruby readings are removed."
        )
    )
    parser.add_argument("input", type=Path, help="Source .epub file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output .md file. Default: the EPUB path with a .md suffix",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the output file if it already exists",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path = args.output or args.input.with_suffix(".md")
    if args.input.resolve() == output_path.resolve():
        raise SystemExit("error: input and output paths must differ")
    if output_path.exists() and not args.force:
        raise SystemExit(
            f"error: output already exists: {output_path} (use --force to overwrite)"
        )

    try:
        result = convert_epub(args.input)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result.markdown, encoding="utf-8", newline="\n")
    except (EpubConversionError, OSError) as exc:
        raise SystemExit(f"error: {exc}") from exc

    print(
        f"Wrote {output_path} "
        f"({result.documents} documents, {result.headings} headings, "
        f"{result.paragraphs} paragraphs)"
    )


if __name__ == "__main__":
    main()
