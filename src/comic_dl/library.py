"""Local SQLite library tracking downloaded series and chapters."""

from __future__ import annotations

import contextlib
import functools
import hashlib
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from .archiver import ARCHIVE_PATTERNS, ARCHIVE_SUFFIXES
from .errors import LibraryError
from .utils import cbz_source_url, normalize_url, normalize_url_key, sanitize_filename

SCHEMA_VERSION = 3

_CREATE_SERIES = """
CREATE TABLE IF NOT EXISTS series (
    series_id     TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    source        TEXT,
    source_site   TEXT,
    source_host   TEXT,
    source_id     TEXT,
    relative_path TEXT NOT NULL,
    last_checked  TEXT,
    last_updated  TEXT,
    created_at    TEXT
)
"""

_CREATE_CHAPTERS = """
CREATE TABLE IF NOT EXISTS chapters (
    series_id     TEXT NOT NULL REFERENCES series(series_id) ON DELETE CASCADE,
    url           TEXT NOT NULL,
    chapter_id    TEXT,
    chapter_no    TEXT,
    title         TEXT,
    cbz           TEXT NOT NULL,
    size_bytes    INTEGER,
    page_count    INTEGER,
    downloaded_at TEXT,
    PRIMARY KEY (series_id, url)
)
"""

_CREATE_INDEX = "CREATE INDEX IF NOT EXISTS idx_chapters_series ON chapters(series_id)"

_CREATE_DOWNLOADS = """
CREATE TABLE IF NOT EXISTS downloads (
    url           TEXT PRIMARY KEY,
    path          TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'cbz'
)
"""


def url_host(url: str) -> str:
    """Lowercased hostname of ``url`` ('' when unparsable)."""
    host = urlsplit(url or "").hostname
    return (host or "").lower()


def url_identity(url: str) -> tuple[str, str]:
    """``(host, path)`` identity for ``url`` — the domain-less part of a URL.

    Mirrors Mihon's ``setUrlWithoutDomain``: ``path`` (with query kept) is
    the stable part of a chapter URL when a site moves to a new domain, so
    ``(host, path)`` lets a domain swap keep matching previously downloaded
    data.
    """
    parsed = urlsplit(url or "")
    host = (parsed.hostname or "").lower()
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    return host, path


def rebase_url(old_url: str, new_host: str) -> str:
    """Re-point ``old_url`` at ``new_host``, preserving scheme + path.

    Used when a source changes domain: the stored chapter URL is rebased so
    identity lookups still match without a full re-scrape. Port and
    userinfo from the old host are dropped; the new host is used verbatim.
    """
    parsed = urlsplit(old_url or "")
    if not parsed.hostname:
        return old_url
    return parsed._replace(netloc=new_host).geturl()


def source_id(name: str, domain: str, version: str) -> str:
    """Stable 64-bit source identifier (MD5 of lowercased name/domain/version).

    Stored on series rows for future source-level features; not surfaced to
    users yet.
    """
    # Identity hash, not security.
    digest = hashlib.md5(  # nosec B324
        f"{name.strip().lower()}|{domain.strip().lower()}|{version.strip()}".encode()
    ).hexdigest()
    return digest[:16]


def library_path(output_dir: Path) -> Path:
    """Location of the library DB for an output root."""
    return Path(output_dir) / ".comic-dl" / "library.db"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _serialized(fn):
    """Serialize a Library method on a per-instance RLock.

    One connection is shared across the event-loop thread and
    ``asyncio.to_thread`` callers (e.g. ``build_have_set``). sqlite3's own
    guard depends on build-time threading mode; the lock makes statement
    groups mutually exclusive unconditionally. RLock because decorated
    methods re-enter via lazy ``open()`` and internal helpers.
    """

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)

    return wrapper


class Library:
    """Best-effort SQLite library for series/chapter metadata.

    The database is strictly optional: any sqlite failure (corrupt file,
    lock, permission problem) silently disables persistence and the caller
    falls back to scanning the filesystem, so downloads never depend on it.

    All URL identity is normalized via :func:`normalize_url` before it is
    stored or queried, so raw values never reach the database.
    """

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._disabled = False
        self._lock = threading.RLock()

    @property
    def _db(self) -> sqlite3.Connection:
        conn = self._conn
        if conn is None:
            self.open()
            conn = self._conn
        if conn is None:
            raise RuntimeError("library database is not open")
        return conn

    # ── lifecycle ───────────────────────────────────────────────

    @_serialized
    def open(self) -> None:
        if self._conn is not None or self._disabled:
            return
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self._db_path), check_same_thread=False,
            )
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.row_factory = sqlite3.Row
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                conn.execute(_CREATE_SERIES)
                conn.execute(_CREATE_CHAPTERS)
                conn.execute(_CREATE_INDEX)
            elif version in (1, 2):
                # Pre-v3 schemas are migrated in-place below.
                pass
            elif version != SCHEMA_VERSION:
                # A newer schema exists; refuse to read it rather than
                # misinterpreting the data.
                conn.close()
                return
            # v1 gained the downloads table; the statement is idempotent.
            conn.execute(_CREATE_DOWNLOADS)
            if version not in (0, SCHEMA_VERSION):
                # v1/v2 -> v3: add the source_host/source_id columns and
                # backfill source_host from the parsed source URL. Newer
                # databases (v3) already have them via _CREATE_SERIES.
                self._migrate_to_v3(conn)
            if version != SCHEMA_VERSION:
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._conn = conn
        except sqlite3.Error:
            self._disabled = True
            self._conn = None

    @staticmethod
    def _migrate_to_v3(conn: sqlite3.Connection) -> None:
        """v1/v2 -> v3: add series columns and backfill ``source_host``.

        The ALTERs are guarded by inspecting ``PRAGMA table_info`` so a
        partially-migrated database is repaired in place rather than
        erroring on a duplicate column. ``source_id`` is left NULL (no
        reliable source metadata is recorded on old rows); it is filled on
        the next ``upsert_series``.
        """
        existing = {
            row[1] for row in conn.execute("PRAGMA table_info(series)").fetchall()
        }
        for name, decl in (("source_host", "TEXT"), ("source_id", "TEXT")):
            if name not in existing:
                conn.execute(f"ALTER TABLE series ADD COLUMN {name} {decl}")
        for row in conn.execute("SELECT series_id, source FROM series").fetchall():
            host = url_host(row["source"] or "")
            if host:
                conn.execute(
                    "UPDATE series SET source_host = ? WHERE series_id = ?",
                    (host, row["series_id"]),
                )
        conn.commit()

    @_serialized
    def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(sqlite3.Error):
                self._conn.close()
            self._conn = None

    def __enter__(self) -> Library:
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def available(self) -> bool:
        return self._conn is not None

    # ── reconciliation ──────────────────────────────────────────

    @_serialized
    def build_have_set(
        self,
        series_id: str,
        series_dir: Path,
        chapters: list[dict],
    ) -> set[str]:
        """Return the normalized URLs of ``chapters`` already present locally.

        A chapter counts as "have" when any of these match:
          1. a recorded DB row whose .cbz file still exists,
          2. a .cbz on disk whose embedded source URL matches,
          3. a .cbz on disk whose filename matches the chapter title
             (base name or a ``Title (post_id)`` disambiguated variant).

        A .cbz with a ``.partial`` marker beside it is never counted as
        "have": the marker means pages are missing and a rerun must retry
        the chapter regardless of how well its URL or title matches.

        Every URL is normalized via :func:`normalize_url`. The filesystem
        scan always runs, so a missing or unreadable DB never loses "have"s.
        """
        have: set[str] = set()

        if self.available:
            try:
                rows = self._db.execute(
                    "SELECT url, cbz FROM chapters WHERE series_id = ?",
                    (series_id,),
                ).fetchall()
                for raw_url, cbz_name in rows:
                    if cbz_name and (series_dir / cbz_name).exists():
                        if self._is_partial(series_dir / cbz_name):
                            continue
                        norm = normalize_url_key(raw_url)
                        if norm:
                            have.add(norm)
            except sqlite3.Error:
                pass

        cbz_names = {
            name
            for name in self._scan_cbz_names(series_dir)
            if not self._is_partial(series_dir / name)
        }
        for raw_url in self._scan_embedded_urls(series_dir, cbz_names):
            norm = normalize_url_key(raw_url)
            if norm:
                have.add(norm)

        for ch in chapters:
            title = self._chapter_label(ch)
            url = ch.get("url") or ""
            if not title or not url:
                continue
            norm = normalize_url_key(url)
            if norm in have:
                continue
            if self._title_matches_cbz(title, cbz_names):
                have.add(norm)

        return have

    # ── reads ───────────────────────────────────────────────────

    @_serialized
    def list_series(self) -> list[dict]:
        """Return every recorded series with chapter counts and sizes.

        Columns: series_id, title, source, source_site, relative_path,
        chapter_count, total_size, last_checked, last_updated.
        """
        if not self.available:
            return []
        try:
            rows = self._db.execute(
                """
                SELECT s.series_id, s.title, s.source, s.source_site,
                       s.relative_path, s.last_checked, s.last_updated,
                       COUNT(c.url) AS chapter_count,
                       COALESCE(SUM(c.size_bytes), 0) AS total_size
                FROM series s
                LEFT JOIN chapters c ON c.series_id = s.series_id
                GROUP BY s.series_id
                ORDER BY COALESCE(s.last_updated, '') DESC, s.title COLLATE NOCASE
                """
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    @_serialized
    def find_series(self, query: str) -> list[dict]:
        """Resolve ``query`` to series rows.

        Matches, in order: exact ``series_id``, exact normalized source URL,
        case-insensitive exact ``title``, then case-insensitive title
        substring. Returns every hit so callers can detect ambiguity.
        """
        if not self.available:
            return []
        try:
            q = query.strip()
            if not q:
                return []
            conn = self._db
            candidates = [
                ("SELECT * FROM series WHERE series_id = ?", q),
            ]
            if q.lower().startswith(("http://", "https://")):
                candidates.append(
                    ("SELECT * FROM series WHERE source = ?", normalize_url(q))
                )
            candidates.extend(
                (
                    ("SELECT * FROM series WHERE title = ? COLLATE NOCASE", q),
                    ("SELECT * FROM series WHERE title LIKE ? COLLATE NOCASE", f"%{q}%"),
                )
            )
            for sql, param in candidates:
                rows = conn.execute(sql, (param,)).fetchall()
                if rows:
                    return [dict(r) for r in rows]
            return []
        except sqlite3.Error:
            return []

    @_serialized
    def get_series(self, series_id: str) -> dict | None:
        if not self.available:
            return None
        try:
            row = self._db.execute(
                "SELECT * FROM series WHERE series_id = ?", (series_id,)
            ).fetchone()
            return dict(row) if row is not None else None
        except sqlite3.Error:
            return None

    @_serialized
    def get_chapters(self, series_id: str) -> list[dict]:
        if not self.available:
            return []
        try:
            rows = self._db.execute(
                "SELECT * FROM chapters WHERE series_id = ? ORDER BY downloaded_at",
                (series_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    @_serialized
    def chapters_since(self, cutoff_iso: str) -> list[dict]:
        """Return chapters with ``downloaded_at >= cutoff_iso`` joined with
        their series title, newest first.

        ``cutoff_iso`` must be an ISO-8601 UTC string in the same
        representation as :func:`_now` so the lexical ``>=`` compare holds.
        """
        if not self.available:
            return []
        try:
            rows = self._db.execute(
                """
                SELECT c.series_id, c.url, c.chapter_no, c.title AS chapter_title,
                       c.cbz, c.size_bytes, c.page_count, c.downloaded_at,
                       s.title AS series_title
                FROM chapters c
                JOIN series s ON s.series_id = c.series_id
                WHERE c.downloaded_at >= ?
                ORDER BY c.downloaded_at DESC
                """,
                (cutoff_iso,),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    @_serialized
    def remove_series(self, series_id: str) -> bool:
        """Delete a series and (via FK cascade) its chapters."""
        if not self.available:
            return False
        try:
            cur = self._db.execute(
                "DELETE FROM series WHERE series_id = ?", (series_id,)
            )
            self._db.commit()
            return cur.rowcount > 0
        except sqlite3.Error as exc:
            raise LibraryError(f"Failed to remove series {series_id}: {exc}") from exc

    # ── writes ──────────────────────────────────────────────────

    @_serialized
    def upsert_series(
        self,
        series_id: str,
        *,
        title: str,
        source: str = "",
        source_site: str = "",
        relative_path: str = "",
        source_id: str = "",
    ) -> None:
        if not self.available:
            return
        try:
            host = url_host(source)
            self._db.execute(
                """
                INSERT INTO series
                    (series_id, title, source, source_site, source_host,
                     source_id, relative_path, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(series_id) DO UPDATE SET
                    title = excluded.title,
                    source = excluded.source,
                    source_site = excluded.source_site,
                    source_host = excluded.source_host,
                    source_id = excluded.source_id,
                    relative_path = excluded.relative_path
                """,
                (
                    series_id,
                    title,
                    source or None,
                    source_site or None,
                    host or None,
                    source_id or None,
                    relative_path,
                    _now(),
                ),
            )
            self._db.commit()
        except sqlite3.Error as exc:
            raise LibraryError(
                f"Failed to record series {series_id}: {exc}"
            ) from exc

    @_serialized
    def upsert_chapter(
        self,
        series_id: str,
        *,
        url: str,
        chapter_id: str = "",
        chapter_no: str | None = None,
        title: str = "",
        cbz: str = "",
        size_bytes: int | None = None,
        page_count: int | None = None,
    ) -> None:
        if not self.available or not url:
            return
        try:
            self._db.execute(
                """
                INSERT INTO chapters
                    (series_id, url, chapter_id, chapter_no, title, cbz,
                     size_bytes, page_count, downloaded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(series_id, url) DO UPDATE SET
                    chapter_id = excluded.chapter_id,
                    chapter_no = excluded.chapter_no,
                    title = excluded.title,
                    cbz = excluded.cbz,
                    size_bytes = excluded.size_bytes,
                    page_count = excluded.page_count,
                    downloaded_at = excluded.downloaded_at
                """,
                (
                    series_id,
                    normalize_url(url),
                    chapter_id or None,
                    chapter_no or None,
                    title or None,
                    cbz or "",
                    size_bytes,
                    page_count,
                    _now(),
                ),
            )
            self._db.commit()
        except sqlite3.Error as exc:
            raise LibraryError(
                f"Failed to record chapter {url}: {exc}"
            ) from exc

    @_serialized
    def set_last_checked(self, series_id: str, ts: str | None = None) -> None:
        self._set_series_field(series_id, "last_checked", ts)

    @_serialized
    def set_last_updated(self, series_id: str, ts: str | None = None) -> None:
        self._set_series_field(series_id, "last_updated", ts)

    @_serialized
    def restore_series(
        self,
        series: dict,
        chapters: list[dict],
    ) -> None:
        """Re-insert a series and its chapters from a removal snapshot.

        ``series`` carries the removed series row (id, title, source,
        source_site, relative_path, last_checked, last_updated,
        created_at); ``chapters`` the removed chapter rows as returned by
        :meth:`get_chapters`. Unlike the upsert helpers this preserves the
        original timestamps so ``list``/``latest`` reflect the historical
        state rather than the restore time. Conflicts are ignored so a
        partial re-insert never clobbers existing data.
        """
        if not self.available:
            return
        try:
            self._db.execute(
                """
                INSERT INTO series
                    (series_id, title, source, source_site, source_host,
                     source_id, relative_path, last_checked, last_updated,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(series_id) DO NOTHING
                """,
                (
                    series["series_id"],
                    series.get("title") or "",
                    series.get("source") or None,
                    series.get("source_site") or None,
                    series.get("source_host") or None,
                    series.get("source_id") or None,
                    series.get("relative_path") or "",
                    series.get("last_checked") or None,
                    series.get("last_updated") or None,
                    series.get("created_at") or _now(),
                ),
            )
            for ch in chapters:
                self._db.execute(
                    """
                    INSERT INTO chapters
                        (series_id, url, chapter_id, chapter_no, title, cbz,
                         size_bytes, page_count, downloaded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(series_id, url) DO NOTHING
                    """,
                    (
                        series["series_id"],
                        normalize_url(ch.get("url") or ""),
                        ch.get("chapter_id") or None,
                        ch.get("chapter_no") or None,
                        ch.get("title") or None,
                        ch.get("cbz") or "",
                        ch.get("size_bytes"),
                        ch.get("page_count"),
                        ch.get("downloaded_at") or _now(),
                    ),
                )
            self._db.commit()
        except sqlite3.Error as exc:
            raise LibraryError(
                f"Failed to restore series {series['series_id']}: {exc}"
            ) from exc

    @_serialized
    def upsert_download(self, url: str, path: str, kind: str = "cbz") -> None:
        """Record a standalone download (single chapter / text post).

        ``path`` is relative to the library output root. The URL is stored
        normalized via :func:`normalize_url`, matching the identity used
        everywhere else. These rows feed the DB-backed skip index so
        standalone downloads are pre-skipped without an on-disk scan.
        """
        if not self.available or not url or not path:
            return
        try:
            self._db.execute(
                """
                INSERT INTO downloads (url, path, kind) VALUES (?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    path = excluded.path,
                    kind = excluded.kind
                """,
                (normalize_url(url), path, kind),
            )
            self._db.commit()
        except sqlite3.Error as exc:
            raise LibraryError(
                f"Failed to record download {url}: {exc}"
            ) from exc

    @_serialized
    def downloaded_index(self, output_dir: Path) -> dict[str, Path]:
        """Map normalized source URL -> downloaded file from the DB alone.

        Covers both series chapters (``chapters`` joined with ``series`` for
        its folder) and standalone downloads (``downloads``). Every row is
        verified against the filesystem first, so deleted files are dropped.
        Returns ``{}`` when the DB is unavailable or errored.
        """
        index: dict[str, Path] = {}
        if not self.available:
            return index
        try:
            rows = self._db.execute(
                """
                SELECT c.url, s.relative_path, c.cbz
                FROM chapters c
                JOIN series s ON s.series_id = c.series_id
                """
            ).fetchall()
            for url, relative_path, cbz in rows:
                path = output_dir / (relative_path or "") / cbz
                if path.is_file():
                    key = normalize_url_key(url)
                    if key:
                        index[key] = path
            for row in self._db.execute(
                "SELECT url, path, kind FROM downloads"
            ).fetchall():
                path = output_dir / row["path"]
                if path.is_file():
                    key = normalize_url_key(row["url"])
                    if key:
                        index[key] = path
        except sqlite3.Error:
            return {}
        return index

    # ── internals ───────────────────────────────────────────────

    def _set_series_field(self, series_id: str, column: str, ts: str | None) -> None:
        if column not in {"last_checked", "last_updated"}:
            raise ValueError(f"unsupported series column: {column!r}")
        if not self.available:
            return
        try:
            # {column} is whitelisted in the guard above, so this is constant SQL.
            self._db.execute(
                f"UPDATE series SET {column} = ? WHERE series_id = ?",  # nosec B608
                (ts or _now(), series_id),
            )
            self._db.commit()
        except sqlite3.Error as exc:
            raise LibraryError(
                f"Failed to update series {series_id}: {exc}"
            ) from exc

    @staticmethod
    def _chapter_label(ch: dict) -> str:
        title = ch.get("title") or ""
        if title:
            return title
        ep = ch.get("episode_no")
        return f"Ep. {ep}" if ep is not None else ""

    @staticmethod
    def _scan_cbz_names(series_dir: Path) -> set[str]:
        try:
            names: set[str] = set()
            for pattern in ARCHIVE_PATTERNS:
                names.update(p.name for p in series_dir.glob(pattern))
            return names
        except OSError:
            return set()

    @staticmethod
    def _is_partial(cbz_path: Path) -> bool:
        """Return True if ``cbz_path`` has a ``.partial`` marker next to it.

        A CBZ with a ``.cbz.partial`` marker is treated as *not* downloaded:
        a rerun retries the missing pages into the same file.
        """
        return cbz_path.with_name(f"{cbz_path.name}.partial").is_file()

    @staticmethod
    def _scan_embedded_urls(series_dir: Path, cbz_names: set[str]) -> list[str]:
        urls: list[str] = []
        for name in cbz_names:
            try:
                url = cbz_source_url(series_dir / name)
            except Exception:
                url = ""
            if url:
                urls.append(url)
        return urls

    @staticmethod
    def _title_matches_cbz(title: str, cbz_names: set[str]) -> bool:
        base = sanitize_filename(title)
        if not base:
            return False
        if any(f"{base}{ext}" in cbz_names for ext in ARCHIVE_SUFFIXES):
            return True
        prefix = f"{base} ("
        return any(
            n.startswith(prefix) and any(n.endswith(f"){ext}") for ext in ARCHIVE_SUFFIXES)
            for n in cbz_names
        )
