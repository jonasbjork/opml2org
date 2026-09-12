#!/usr/bin/env python3

from __future__ import annotations

import argparse
import html
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


MARKDOWN_LINK_RE = re.compile(
    r"^\[(https?://[^\]]+)\]\((https?://[^)]+)\)$"
)


def clean_text(value: str | None) -> str:
    if not value:
        return ""

    value = html.unescape(value)
    value = value.replace("\r\n", "\n")
    value = value.replace("\r", "\n")

    return value.strip()


def convert_inline_text(text: str) -> str:
    """
    Convert simple inline constructs found in OmniOutliner exports
    into Org syntax.
    """
    text = clean_text(text)

    # Markdown-style URL exported/stored in the outline:
    # [https://example.com](https://example.com)
    match = MARKDOWN_LINK_RE.match(text)

    if match:
        label, url = match.groups()
        return f"[[{url}][{label}]]"

    # Bare URL
    if text.startswith(("http://", "https://")) and "\n" not in text:
        return f"[[{text}]]"

    return text


def is_body_text(element: ET.Element, text: str) -> bool:
    """
    Decide whether an OmniOutliner row should become normal Org text
    rather than an Org heading.

    OmniOutliner stores every row as <outline>, including rows that
    contain paragraphs of text.
    """

    children = list(element.findall("outline"))

    # Anything with children must remain a heading so the tree survives.
    if children:
        return False

    # Multiline leaf nodes are almost certainly prose / notes.
    if "\n" in text:
        return True

    # Very long leaf rows are more useful as body text than headings.
    if len(text) > 160:
        return True

    return False


def format_body_text(text: str) -> list[str]:
    """
    Preserve paragraphs and line breaks from an OmniOutliner text row.
    """
    text = clean_text(text)

    if not text:
        return []

    return [line.rstrip() for line in text.splitlines()]


def outline_to_org(
    element: ET.Element,
    level: int = 1,
) -> list[str]:

    output: list[str] = []

    text = clean_text(
        element.attrib.get("text")
        or element.attrib.get("title")
        or element.attrib.get("_text")
    )

    # Ignore completely empty OmniOutliner rows.
    if not text:
        for child in element.findall("outline"):
            output.extend(outline_to_org(child, level))
        return output

    #
    # Long/multiline leaf row -> normal body text.
    #
    if is_body_text(element, text):
        output.append("")
        output.extend(format_body_text(text))
        output.append("")
        return output

    #
    # Normal outline row -> Org heading.
    #
    text = convert_inline_text(text)

    output.append(f"{'*' * level} {text}")

    #
    # OmniOutliner / OPML notes.
    #
    note = (
        element.attrib.get("_note")
        or element.attrib.get("note")
        or element.attrib.get("description")
    )

    if note:
        note = clean_text(note)

        if note:
            output.append("")
            output.extend(note.splitlines())
            output.append("")

    #
    # Preserve useful extra attributes as Org properties.
    #
    ignored_attributes = {
        "text",
        "title",
        "_text",
        "_note",
        "note",
        "description",
    }

    properties = {
        key: clean_text(value)
        for key, value in element.attrib.items()
        if key not in ignored_attributes and clean_text(value)
    }

    if properties:
        output.append(":PROPERTIES:")

        for key, value in sorted(properties.items()):
            key = re.sub(r"[^A-Za-z0-9_]", "_", key.upper())
            value = value.replace("\n", " ")

            output.append(f":{key}: {value}")

        output.append(":END:")

    #
    # Children
    #
    for child in element.findall("outline"):
        output.extend(outline_to_org(child, level + 1))

    return output


def parse_opml(path: Path) -> str:
    try:
        tree = ET.parse(path)
    except ET.ParseError as error:
        raise RuntimeError(
            f"Invalid OPML/XML in {path}: {error}"
        ) from error

    root = tree.getroot()

    body = root.find("body")

    if body is None:
        raise RuntimeError(
            f"No <body> element found in {path}"
        )

    output: list[str] = []

    #
    # OPML metadata
    #
    head = root.find("head")

    if head is not None:

        title_element = head.find("title")

        if (
            title_element is not None
            and title_element.text
        ):
            title = clean_text(title_element.text)

            if title:
                output.append(f"#+title: {title}")

        date_created = head.find("dateCreated")

        if (
            date_created is not None
            and date_created.text
        ):
            date = clean_text(date_created.text)

            if date:
                output.append(
                    f"#+date_created: {date}"
                )

        if output:
            output.append("")

    #
    # Outline
    #
    for outline in body.findall("outline"):
        output.extend(
            outline_to_org(outline, level=1)
        )

    #
    # Collapse runs of excessive blank lines.
    #
    cleaned: list[str] = []

    previous_blank = False

    for line in output:

        blank = not line.strip()

        if blank and previous_blank:
            continue

        cleaned.append(line)
        previous_blank = blank

    while cleaned and not cleaned[-1].strip():
        cleaned.pop()

    return "\n".join(cleaned) + "\n"


def convert_file(
    source: Path,
    destination: Path,
    overwrite: bool = False,
) -> None:

    if destination.exists() and not overwrite:
        raise FileExistsError(
            f"{destination} already exists "
            "(use --overwrite)"
        )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    content = parse_opml(source)

    destination.write_text(
        content,
        encoding="utf-8",
    )

    print(f"{source} -> {destination}")


def convert_directory(
    source: Path,
    destination: Path,
    overwrite: bool = False,
) -> tuple[int, int]:

    success = 0
    failed = 0

    files = sorted(source.rglob("*.opml"))

    for opml_file in files:

        relative = opml_file.relative_to(source)

        org_file = (
            destination
            / relative.with_suffix(".org")
        )

        try:
            convert_file(
                opml_file,
                org_file,
                overwrite,
            )

            success += 1

        except Exception as error:

            print(
                f"ERROR: {opml_file}: {error}",
                file=sys.stderr,
            )

            failed += 1

    return success, failed


def main() -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Convert OmniOutliner OPML exports "
            "to Emacs Org mode."
        )
    )

    parser.add_argument(
        "source",
        type=Path,
        help="OPML file or directory",
    )

    parser.add_argument(
        "destination",
        nargs="?",
        type=Path,
        help="Output .org file or directory",
    )

    parser.add_argument(
        "-f",
        "--overwrite",
        action="store_true",
        help="Overwrite existing Org files",
    )

    args = parser.parse_args()

    source = args.source.expanduser()

    if not source.exists():

        print(
            f"Source does not exist: {source}",
            file=sys.stderr,
        )

        return 1

    #
    # Single file
    #
    if source.is_file():

        destination = (
            args.destination.expanduser()
            if args.destination
            else source.with_suffix(".org")
        )

        try:
            convert_file(
                source,
                destination,
                args.overwrite,
            )

        except Exception as error:

            print(
                f"ERROR: {error}",
                file=sys.stderr,
            )

            return 1

        return 0

    #
    # Directory
    #
    destination = (
        args.destination.expanduser()
        if args.destination
        else source.parent / f"{source.name}-org"
    )

    success, failed = convert_directory(
        source,
        destination,
        args.overwrite,
    )

    print()
    print(f"Converted: {success}")
    print(f"Failed:    {failed}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

