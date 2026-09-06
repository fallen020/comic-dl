"""Comic archive creation: write to a temp file, verify the archive, then atomically rename.

``.cbz`` and ``.zip`` are zip containers; ``.cbt`` is a tar container. All
formats share the same page naming, size-first dedup, ComicInfo.xml, and
atomic-write-with-verification guarantees.
"""

from __future__ import annotations

import contextlib
import io
import os
import tarfile
import tempfile
from collections.abc import Callable, Iterator
from functools import partial
from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from .comicinfo import generate_comicinfo_xml
from .models import ImageItem, PostMetadata
from .ui import TRACE, vlog
from .utils import MAGIC_MAX, verify_image_bytes

#: Archive suffixes `create_archive` can produce.
ARCHIVE_SUFFIXES = frozenset({".cbz", ".zip", ".cbt"})

#: Glob patterns covering every archive a download/reconciliation pass may meet.
ARCHIVE_PATTERNS = ("*.cbz", "*.zip", "*.cbt")


def parse_compression(value: str) -> tuple[int, int]:
    """Map a ``--compress`` / ``[archive] compression`` value to zip arguments.

    Accepts ``stored`` (no compression), ``deflate`` (level 6), and
    ``deflate:<0-9>`` for an explicit level. Returns ``(compress_type, level)``
    suitable for ``ZipFile(..., compresslevel=...)``. Anything else raises
    ``ValueError`` so a typo fails loudly at startup instead of silently
    producing a different archive. Tar archives (``.cbt``) are never
    compressed, so the value is parsed but unused for them.
    """
    if not isinstance(value, str):
        raise ValueError(
            f"Invalid compression {value!r}: expected stored or deflate[:level]"
        )
    v = value.strip().lower()
    if v == "stored":
        return ZIP_STORED, 0
    if v == "deflate":
        return ZIP_DEFLATED, 6
    if v.startswith("deflate:"):
        level = v[len("deflate:") :]
        if level.isdigit() and 0 <= int(level) <= 9:
            return ZIP_DEFLATED, int(level)
        raise ValueError(f"Invalid deflate level {level!r}: expected 0-9")
    raise ValueError(f"Invalid compression {value!r}: expected stored or deflate[:level]")


def _safe_source_path(source_dir: Path, src_name: str) -> Path | None:
    """Resolve a page filename to a path strictly inside ``source_dir``.

    Downloader-supplied names are always basenames, but plugin scrapers are
    arbitrary code and ``ImageItem.filename`` could try to escape the download
    directory with ``../``, absolute paths, or drive-letter tricks. Refuse any
    name that is not a plain basename so the archive can never read — or
    remove a duplicate of — files outside ``source_dir``.
    """
    if not src_name or src_name in (".", ".."):
        return None
    if src_name.startswith(("/", "\\")) or os.sep in src_name:
        return None
    if os.altsep and os.altsep in src_name:
        return None
    return source_dir / src_name


def _packed_members(
    images: list[ImageItem],
    source_dir: Path,
    verified: dict[str, str],
    skipped: list[str],
) -> Iterator[tuple[int, bytes, str]]:
    """Yield ``(index, content, arcname)`` for pages that pass validation.

    Appends human-readable skip reasons to ``skipped`` for missing, invalid,
    escape, and duplicate pages. One stat per file is taken up front so the
    size map and the dedup decision reuse it instead of re-stat'ing every
    page. Duplicate elimination is deterministic: pages are considered in
    order and the first page to claim a hash keeps its name.

    Every yielded page is a single-read snapshot: the exact bytes that are
    magic-verified (against the actual content, not :func:`verify_image_file`
    on a path) and hashed are the bytes the writers later embed, so a file
    swapped between validation and packing cannot leak into the archive.
    """
    sizes: dict[str, int] = {}
    missing: set[str] = set()
    for item in images:
        src_name = item.filename
        src = _safe_source_path(source_dir, src_name)
        if src is None:
            missing.add(src_name)
            continue
        try:
            sizes[src_name] = src.stat().st_size
        except OSError:
            missing.add(src_name)

    # Only hash files whose size is shared: distinct sizes are distinct content
    # for all practical purposes, so hashing every page would be pure waste.
    size_freq: dict[int, int] = {}
    for s in sizes.values():
        size_freq[s] = size_freq.get(s, 0) + 1

    seen_hashes: set[str] = set()
    for idx, item in enumerate(images, start=1):
        src_name = item.filename
        src = _safe_source_path(source_dir, src_name)
        if src is None or src_name in missing or src_name not in sizes:
            skipped.append(f"{src_name} (missing)")
            continue
        try:
            data = src.read_bytes()
        except OSError:
            skipped.append(f"{src_name} (missing)")
            continue
        # The format is taken from the bytes actually read; the download-run
        # cache is only a fallback for content with an unrecognized header.
        fmt = verify_image_bytes(data[:MAGIC_MAX]) or verified.get(src_name)
        if fmt is None:
            skipped.append(f"{src_name} (not a valid image)")
            src.unlink(missing_ok=True)
            continue
        if size_freq.get(sizes[src_name], 0) > 1:
            fhash = sha256(data).hexdigest()
            if fhash in seen_hashes:
                skipped.append(f"{src_name} (duplicate)")
                src.unlink(missing_ok=True)
                continue
            seen_hashes.add(fhash)

        ext = "." + fmt if fmt else (src.suffix or ".jpg")
        yield idx, data, f"Page_{idx:04d}{ext}"


def _comicinfo_bytes(
    page_count: int,
    series_title: str,
    chapter_title: str,
    source_url: str,
    chapter_number: str | None,
    volume_number: str | None,
    series_meta: PostMetadata | None = None,
) -> str | None:
    if not (series_title or chapter_title):
        return None
    return generate_comicinfo_xml(
        series_title=series_title or chapter_title,
        chapter_title=chapter_title or series_title,
        page_count=page_count,
        source_url=source_url,
        chapter_number=chapter_number,
        volume_number=volume_number,
        description=(getattr(series_meta, "description", "") if series_meta else ""),
        authors=getattr(series_meta, "authors", None) if series_meta else None,
        artists=getattr(series_meta, "artists", None) if series_meta else None,
        colorists=getattr(series_meta, "colorists", None) if series_meta else None,
        genres=getattr(series_meta, "genres", None) if series_meta else None,
        language=getattr(series_meta, "language", None) if series_meta else None,
        publisher=getattr(series_meta, "publisher", None) if series_meta else None,
        status=getattr(series_meta, "status", None) if series_meta else None,
        reading_direction=(
            getattr(series_meta, "reading_direction", None) if series_meta else None
        ),
        community_rating=(
            getattr(series_meta, "community_rating", None) if series_meta else None
        ),
        year=getattr(series_meta, "year", None) if series_meta else None,
        has_cover=True,
    )


def _write_zip(
    tmp_path: Path,
    members: Iterator[tuple[int, bytes, str]],
    compression: str,
    on_packed: Callable[[int, int], None] | None,
    total: int,
    comicinfo: Callable[[int], str | None],
) -> int:
    compress_type, compress_level = parse_compression(compression)
    added = 0
    with ZipFile(tmp_path, 'w', compress_type, compresslevel=compress_level) as zf:
        for _idx, data, arcname in members:
            zf.writestr(
                arcname, data, compress_type=compress_type, compresslevel=compress_level
            )
            added += 1
            if on_packed is not None:
                on_packed(added, total)
        xml = comicinfo(added)
        if xml is not None:
            zf.writestr("ComicInfo.xml", xml)
    return added


def _write_tar(
    tmp_path: Path,
    members: Iterator[tuple[int, bytes, str]],
    on_packed: Callable[[int, int], None] | None,
    total: int,
    comicinfo: Callable[[int], str | None],
) -> int:
    added = 0
    with tarfile.open(tmp_path, "w") as tf:
        for _idx, data, arcname in members:
            info = tarfile.TarInfo(arcname)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
            added += 1
            if on_packed is not None:
                on_packed(added, total)
        xml = comicinfo(added)
        if xml is not None:
            data = xml.encode("utf-8")
            info = tarfile.TarInfo("ComicInfo.xml")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return added


def _verify_zip(tmp_path: Path) -> None:
    try:
        with ZipFile(tmp_path, "r") as zf:
            corrupted = zf.testzip()
    except Exception as exc:
        raise ValueError(f"Archive verification failed: {exc}") from exc
    if corrupted is not None:
        raise ValueError(f"Archive corrupted: {corrupted}")


def _verify_tar(tmp_path: Path) -> None:
    # tarfile has no test(); read every member's bytes so a truncated or
    # unreadable archive fails here, before the atomic rename.
    try:
        with tarfile.open(tmp_path, "r") as tf:
            for member in tf:
                if not member.isfile():
                    continue
                f = tf.extractfile(member)
                if f is None:
                    continue
                while f.read(65536):
                    pass
    except Exception as exc:
        raise ValueError(f"Archive verification failed: {exc}") from exc


def create_archive(
    images: list[ImageItem],
    source_dir: Path,
    output_path: Path,
    series_title: str = "",
    chapter_title: str = "",
    source_url: str = "",
    verified_formats: dict[str, str] | None = None,
    chapter_number: str | None = None,
    volume_number: str | None = None,
    compression: str = "stored",
    on_packed: Callable[[int, int], None] | None = None,
    series_meta: PostMetadata | None = None,
) -> tuple[int, list[str]]:
    """Pack verified images into a comic archive, skipping bad or duplicate pages.

    The format is derived from ``output_path.suffix``: ``.cbz``/``.zip`` are
    zip archives, ``.cbt`` a tar archive. Writes to a unique sibling temp file,
    reopens it to verify integrity, then atomically replaces the destination
    (:func:`os.replace`) so a partial archive is never left behind and two
    concurrent runs never collide on a fixed temp name. If no page can be
    packed at all the archive would hold only ComicInfo.xml (or nothing), so a
    ``ValueError`` is raised and the existing output is left untouched.

    Every page is read once and verified, deduplicated, and written from that
    single snapshot, so a file that changes mid-pack cannot leak into the
    archive; relying on the download run's ``verified_formats`` cache is not
    enough precisely because those files may have been swapped since. Page
    filenames must be plain basenames — anything containing a path separator
    or an ``..`` component is refused — so plugin-supplied names can never
    read or delete files outside ``source_dir``.

    Args:
        images: The ordered page list.
        source_dir: Directory holding the downloaded image files.
        output_path: Destination path (``.cbz``, ``.zip``, or ``.cbt``; its
            parent is created).
        series_title: Written to ComicInfo.xml alongside ``chapter_title``.
        chapter_title: Written to ComicInfo.xml alongside ``series_title``.
        source_url: Original page URL, recorded in the ComicInfo.xml ``Web`` tag.
        verified_formats: ``{source_name: format}`` cache from the download run,
            used as a fallback only; content is always re-verified from the
            bytes being packed.
        chapter_number: Chapter number for ComicInfo.xml.
        volume_number: Volume number for ComicInfo.xml.
        compression: ``stored`` (default, no compression) or ``deflate[:level]``
            — see :func:`parse_compression`. Applies to zip archives only;
            tar archives are never compressed. The default is deliberately
            unchanged so users must opt into the size-vs-time trade-off.
        on_packed: Optional callback invoked after each page is written, with
            ``(packed_so_far, total_pages)``. Runs on the packing thread; keep
            it cheap (e.g. updating a counter for progress feedback).
        series_meta: Optional scraped metadata (authors, genres, summary, ...)
            whose series-wide fields are embedded in the archive's
            ComicInfo.xml alongside the per-chapter fields.

    Returns:
        A ``(added, skipped)`` pair: how many pages were archived and the
        names (with reason) of the pages that were skipped or cleaned up.

    Raises:
        ValueError: If ``output_path.suffix`` is unsupported, or if every page
            was skipped (nothing would be packed).
    """
    fmt = output_path.suffix.lower()
    if fmt not in ARCHIVE_SUFFIXES:
        raise ValueError(
            f"Unsupported archive format {output_path.suffix!r}: "
            "expected .cbz, .zip, or .cbt"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    vlog(TRACE, f"archive: packing {len(images)} files → {output_path.name}")
    skipped: list[str] = []

    tmp_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
        )
        os.close(fd)
        tmp_path = Path(tmp_name)

        members = _packed_members(images, source_dir, verified_formats or {}, skipped)
        total = len(images)
        comicinfo = partial(
            _comicinfo_bytes,
            series_title=series_title,
            chapter_title=chapter_title,
            source_url=source_url,
            chapter_number=chapter_number,
            volume_number=volume_number,
            series_meta=series_meta,
        )

        if fmt == ".cbt":
            added = _write_tar(tmp_path, members, on_packed, total, comicinfo)
            _verify_tar(tmp_path)
        else:
            added = _write_zip(tmp_path, members, compression, on_packed, total, comicinfo)
            _verify_zip(tmp_path)
        if added == 0:
            raise ValueError(f"No pages could be packed ({output_path.name})")
        os.replace(tmp_path, output_path)
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)

    return added, skipped

