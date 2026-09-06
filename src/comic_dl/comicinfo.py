"""ComicInfo.xml generation for chapter archives and series folders."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # nosec B405

_INVALID_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

TACHIHOMI_NS = "http://www.w3.org/2001/XMLSchema"

ET.register_namespace("ty", TACHIHOMI_NS)

_TACHIHOMI_STATUS_TAG = "{" + TACHIHOMI_NS + "}PublishingStatusTachiyomi"

_STATUS_ALIASES = {
    "ongoing": "Ongoing",
    "in progress": "Ongoing",
    "currently publishing": "Ongoing",
    "publishing": "Ongoing",
    "completed": "Completed",
    "complete": "Completed",
    "finished": "Completed",
    "ended": "Completed",
    "licensed": "Licensed",
    "publishing finished": "Publishing finished",
    "cancelled": "Cancelled",
    "canceled": "Cancelled",
    "on hiatus": "On hiatus",
    "on-hiatus": "On hiatus",
    "hiatus": "On hiatus",
    "paused": "On hiatus",
}

_PLACEHOLDER_CHAPTER_TITLE = re.compile(
    r"^chapter(?:\s*(?:#|no\.?)?\s*\d+)?$", re.IGNORECASE
)


def normalize_status(value: str | None) -> str | None:
    """Map a scraper status string onto the ComicInfo vocabulary.

    Returns ``None`` for missing or unrecognized values so callers can omit
    the status elements instead of asserting a status the scraper does not
    actually understand.
    """
    if not value:
        return None
    return _STATUS_ALIASES.get(value.strip().lower())


def _sanitize_xml_text(text: str) -> str:
    return _INVALID_XML_CHARS.sub("", text)


_MANGA_BY_DIRECTION = {
    "rtl": "YesAndRightToLeft",
    "ltr": "No",
}


def _new_root() -> ET.Element:
    """A ``ComicInfo`` root element with the standard namespace attributes."""
    root = ET.Element("ComicInfo")
    root.set("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
    root.set("xmlns:xsd", "http://www.w3.org/2001/XMLSchema")
    return root


def _add_text(root: ET.Element, tag: str, value: str | None) -> None:
    """Append ``tag`` holding sanitized ``value``; skip ``None``/empty.

    Optional metadata tags are only emitted when they carry a value, so an
    absent field never leaves a stray empty element in the archive.
    """
    if value is None:
        return
    text = _sanitize_xml_text(value)
    if not text:
        return
    elem = ET.SubElement(root, tag)
    elem.text = text


def _add_joined(root: ET.Element, tag: str, values: list[str] | None) -> None:
    """Append one ``tag`` element per creator role, comma-joined.

    The schema forbids repeating an element and readers typically honour only
    the first occurrence, so each role appears exactly once with its names
    comma-separated.
    """
    if values:
        _add_text(root, tag, ", ".join(values))


def _add_shared(
    root: ET.Element,
    *,
    description: str,
    source_url: str,
    genres: list[str] | None,
    authors: list[str] | None,
    artists: list[str] | None,
    colorists: list[str] | None,
    publisher: str | None,
    status: str | None,
    language: str | None,
    reading_direction: str | None,
    community_rating: str | None,
    year: int | None,
) -> None:
    """Write the metadata tags shared by chapter and series documents.

    Single place where those fields become XML, so the two writers cannot
    drift. Tag order is canonical; readers resolve elements by name.
    """
    if description.strip():
        _add_text(root, "Summary", description)
    _add_text(root, "Web", source_url)
    _add_joined(root, "Genre", genres)
    _add_joined(root, "Writer", authors)
    _add_joined(root, "Artist", artists)
    _add_joined(root, "Colorist", colorists)
    _add_text(root, "Publisher", publisher)

    status_norm = normalize_status(status)
    _add_text(root, "Status", status_norm)
    _add_text(root, _TACHIHOMI_STATUS_TAG, status_norm)

    _add_text(root, "LanguageISO", language)

    manga = _MANGA_BY_DIRECTION.get(reading_direction or "")
    if manga is not None:
        _add_text(root, "Manga", manga)

    _add_text(root, "CommunityRating", community_rating)
    if year is not None:
        _add_text(root, "Year", str(int(year)))


def generate_comicinfo_xml(
    series_title: str,
    chapter_title: str,
    page_count: int,
    source_url: str = "",
    description: str = "",
    chapter_number: str | None = None,
    volume_number: str | None = None,
    authors: list[str] | None = None,
    artists: list[str] | None = None,
    colorists: list[str] | None = None,
    genres: list[str] | None = None,
    language: str | None = None,
    publisher: str | None = None,
    status: str | None = None,
    reading_direction: str | None = None,
    community_rating: float | None = None,
    year: int | None = None,
    has_cover: bool = False,
) -> str:
    """Build a chapter-level ComicInfo.xml document.

    Args:
        series_title: Series name (also used as ``Title`` fallback).
        chapter_title: Chapter title.
        page_count: Number of pages, written to ``PageCount``.
        source_url: Original page URL for the ``Web`` tag.
        description: Series summary.
        chapter_number: Chapter number; falls back to the first digits found
            in ``chapter_title``.
        volume_number: Volume number.
        authors: ``Writer`` names, comma-joined.
        artists: ``Artist`` names, comma-joined.
        colorists: ``Colorist`` names, comma-joined.
        genres: ``Genre`` values, comma-joined.
        language: ISO language code for ``LanguageISO``.
        publisher: ``Publisher`` name.
        status: Scraper status, normalized to the ComicInfo vocabulary
            (unrecognized values are omitted).
        reading_direction: ``"rtl"`` maps to ``Manga=YesAndRightToLeft``;
            anything else to ``Manga=No``.
        community_rating: 0-10 rating for ``CommunityRating``.
        year: Publication year for ``Year``.
        has_cover: When true, marks page 0 as ``FrontCover`` in ``Pages``.

    Returns:
        The serialized XML document, including the XML declaration.
    """
    root = _new_root()

    # Series and Title are always present (even blank) so a reader always
    # finds the entry-point tags; everything else is emitted only when set.
    # A fabricated "Chapter N" fallback title carries no information, so it
    # degrades to the series title instead of filling ``Title`` with noise.
    clean_title = chapter_title.strip()
    if (
        not clean_title
        or clean_title == series_title.strip()
        or _PLACEHOLDER_CHAPTER_TITLE.match(clean_title)
    ):
        clean_title = series_title
    series = ET.SubElement(root, "Series")
    series.text = _sanitize_xml_text(series_title)
    title = ET.SubElement(root, "Title")
    title.text = _sanitize_xml_text(clean_title)

    number = chapter_number or _extract_number(chapter_title)
    if number is not None:
        _add_text(root, "Number", number)
    if volume_number is not None:
        _add_text(root, "Volume", volume_number)
    _add_text(root, "PageCount", str(page_count))

    rating = str(community_rating) if community_rating is not None else None
    _add_shared(
        root,
        description=description,
        source_url=source_url,
        genres=genres,
        authors=authors,
        artists=artists,
        colorists=colorists,
        publisher=publisher,
        status=status,
        language=language,
        reading_direction=reading_direction,
        community_rating=rating,
        year=year,
    )

    if page_count > 0:
        pages = ET.SubElement(root, "Pages")
        for page_index in range(page_count):
            page = ET.SubElement(pages, "Page")
            page.set("Image", str(page_index))
            if has_cover and page_index == 0:
                page.set("Type", "FrontCover")

    raw = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + raw


def generate_series_comicinfo_xml(
    series_title: str,
    source_url: str = "",
    description: str = "",
    authors: list[str] | None = None,
    artists: list[str] | None = None,
    colorists: list[str] | None = None,
    genres: list[str] | None = None,
    publisher: str | None = None,
    status: str | None = None,
    language: str | None = None,
    reading_direction: str | None = None,
    community_rating: float | None = None,
    year: int | None = None,
) -> str:
    """A series-level ComicInfo.xml written beside the chapter archives.

    Carries everything that applies to the whole series — the summary,
    creators, genres, publication details, and reading direction — and omits
    per-chapter fields (Number, Volume, PageCount, Pages). ``Title`` is the
    series name so a comic reader that treats the file as an entry point
    still has something sensible to display.
    """
    root = _new_root()
    series = ET.SubElement(root, "Series")
    series.text = _sanitize_xml_text(series_title)
    title = ET.SubElement(root, "Title")
    title.text = _sanitize_xml_text(series_title)

    rating = str(community_rating) if community_rating is not None else None
    _add_shared(
        root,
        description=description,
        source_url=source_url,
        genres=genres,
        authors=authors,
        artists=artists,
        colorists=colorists,
        publisher=publisher,
        status=status,
        language=language,
        reading_direction=reading_direction,
        community_rating=rating,
        year=year,
    )

    raw = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + raw


def _extract_number(chapter_title: str) -> str | None:
    m = re.search(r"(\d+)", chapter_title)
    if m:
        return m.group(1)
    return None
