"""Data contracts shared by scrapers, the downloader, and archive metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

from .utils import image_source_name, sanitize_filename


@dataclass(slots=True)
class ImageItem:
    """One downloadable image: its URL, page number, and on-disk filename.

    ``source_url``, when set, is the page that minted ``url`` (e.g. an
    e-hentai ``/s/<token>/<gid>-<n>`` page behind a keystamped image link).
    The downloader uses it to re-resolve a fresh link when a retry hits an
    expired one instead of re-fetching the same dead URL.
    """

    url: str
    page_number: int
    filename: str = ""
    source_url: str = ""

    @classmethod
    def from_url(cls, url: str, page_number: int | None = None) -> ImageItem | None:
        match = re.search(r'[?&]f=([^&]+)', url)
        if match:
            page_name = sanitize_filename(unquote(match.group(1)))
            num_match = re.search(r'(\d+)', page_name)
            if num_match:
                return cls(
                    url=url,
                    page_number=int(num_match.group(1)),
                    filename=page_name,
                )
            if page_number is not None:
                return cls(url=url, page_number=page_number, filename=page_name)

        parsed = urlparse(url)
        raw = parsed.path.rstrip('/').rsplit('/', 1)[-1]
        if not raw or page_number is None:
            return None
        filename = sanitize_filename(unquote(raw))
        return cls(url=url, page_number=page_number, filename=filename)


@dataclass(slots=True)
class ChapterInfo:
    """Titles and descriptive metadata of a single chapter."""

    series_title: str
    chapter_title: str

    chapter_number: str | None = None
    volume_number: str | None = None

    description: str = ""

    total_pages: int | None = None

    authors: list[str] = field(default_factory=list)
    artists: list[str] = field(default_factory=list)
    colorists: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)

    publisher: str | None = None
    status: str | None = None

    language: str | None = None
    reading_direction: str | None = None

    community_rating: float | None = None
    year: int | None = None

    estimated_size: int | None = None


@dataclass(slots=True)
class SourceInfo:
    """Where the chapter came from (service + opaque post/user ids)."""

    url: str
    service: str

    post_id: str = ""
    user_id: str = ""


@dataclass(slots=True)
class ScrapedChapter:
    """The full result of scraping a single chapter or gallery."""

    info: ChapterInfo
    source: SourceInfo

    images: list[ImageItem] = field(default_factory=list)

    cover_url: str = ""

    text_content: str | None = None


@dataclass(slots=True)
class PostMetadata:
    """Core model for a download: metadata + filename-bearing images."""

    series_title: str
    chapter_title: str
    images: list[ImageItem] = field(default_factory=list)
    service: str = ""
    user_id: str = ""
    post_id: str = ""
    total_pages: int | None = None
    description: str = ""
    cover_url: str = ""
    chapter_number: str | None = None
    volume_number: str | None = None
    authors: list[str] = field(default_factory=list)
    artists: list[str] = field(default_factory=list)
    colorists: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    publisher: str | None = None
    status: str | None = None
    language: str | None = None
    reading_direction: str | None = None
    community_rating: float | None = None
    year: int | None = None
    estimated_size: int = 0
    warnings: list[str] = field(default_factory=list)
    text_content: str | None = None


@dataclass(slots=True)
class SeriesMetadata:
    """Core model for a series listing: its chapters as ordered dicts.

    Each chapter dict carries ``title`` / ``episode_no`` / ``url``.
    """

    series_title: str
    chapters: list[dict] = field(default_factory=list)
    description: str = ""
    cover_url: str = ""
    title_no: str = ""


def chapter_to_post_metadata(sc: ScrapedChapter) -> PostMetadata:
    """Convert a scraped chapter into the core ``PostMetadata`` model.

    Images are re-keyed with their stable source filename, and
    ``total_pages`` defaults to the image count when the scraper did not
    report a number.
    """
    images = [
        ImageItem(
            url=img.url,
            page_number=img.page_number,
            filename=image_source_name(img.page_number, img.url),
        )
        for img in sc.images
    ]

    return PostMetadata(
        series_title=sc.info.series_title,
        chapter_title=sc.info.chapter_title,
        images=images,
        service=sc.source.service,
        user_id=sc.source.user_id,
        post_id=sc.source.post_id,
        total_pages=sc.info.total_pages if sc.info.total_pages is not None else len(sc.images),
        description=sc.info.description,
        cover_url=sc.cover_url,
        chapter_number=sc.info.chapter_number,
        volume_number=sc.info.volume_number,
        authors=sc.info.authors,
        artists=sc.info.artists,
        colorists=sc.info.colorists,
        genres=sc.info.genres,
        publisher=sc.info.publisher,
        status=sc.info.status,
        language=sc.info.language,
        reading_direction=sc.info.reading_direction,
        community_rating=sc.info.community_rating,
        year=sc.info.year,
        estimated_size=sc.info.estimated_size or 0,
        text_content=sc.text_content,
    )
