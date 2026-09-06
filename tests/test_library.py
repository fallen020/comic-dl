from __future__ import annotations

import io
import sqlite3
import tarfile
import zipfile
from pathlib import Path

import pytest

from comic_dl.library import (
    Library,
    library_path,
    rebase_url,
    source_id,
    url_host,
    url_identity,
)
from comic_dl.utils import normalize_url


def _make_cbz(path: Path, web: str = "") -> None:
    with zipfile.ZipFile(path, "w") as zf:
        if web:
            zf.writestr(
                "ComicInfo.xml",
                f"<ComicInfo><Title>T</Title><Web>{web}</Web></ComicInfo>",
            )
        else:
            zf.writestr("ComicInfo.xml", "<ComicInfo><Title>T</Title></ComicInfo>")


def _make_cbt(path: Path, web: str = "") -> None:
    body = (
        f"<ComicInfo><Title>T</Title><Web>{web}</Web></ComicInfo>"
        if web
        else "<ComicInfo><Title>T</Title></ComicInfo>"
    ).encode("utf-8")
    with tarfile.open(path, "w") as tf:
        info = tarfile.TarInfo("ComicInfo.xml")
        info.size = len(body)
        tf.addfile(info, io.BytesIO(body))


class TestLibraryOpen:
    def test_schema_created_and_idempotent(self, tmp_path):
        db = tmp_path / "library.db"
        lib = Library(db)
        lib.open()
        assert lib.available
        assert db.exists()
        with sqlite3.connect(str(db)) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name "
                    "IN ('series', 'chapters', 'downloads')"
                )
            }
            assert tables == {"series", "chapters", "downloads"}
        lib.close()

        # Re-opening an existing schema is a clean no-op.
        lib.open()
        assert lib.available
        lib.close()

    def test_future_schema_disables_db(self, tmp_path):
        db = tmp_path / "library.db"
        with sqlite3.connect(str(db)) as conn:
            conn.execute("PRAGMA user_version = 999")
        lib = Library(db)
        lib.open()
        assert not lib.available
        lib.close()

    def test_corrupt_db_disables_db(self, tmp_path):
        db = tmp_path / "library.db"
        db.write_bytes(b"not a sqlite database")
        lib = Library(db)
        lib.open()
        assert not lib.available
        lib.close()

    def test_library_path_location(self, tmp_path):
        assert library_path(tmp_path) == tmp_path / ".comic-dl" / "library.db"


class TestUpserts:
    def test_upsert_series_round_trip(self, tmp_path):
        lib = Library(tmp_path / "lib.db")
        lib.open()
        lib.upsert_series(
            "webtoons.com:1", title="T", source="https://x/",
            source_site="webtoons.com", relative_path="T",
        )
        lib.set_last_checked("webtoons.com:1")
        lib.set_last_updated("webtoons.com:1")
        with sqlite3.connect(str(tmp_path / "lib.db")) as conn:
            row = conn.execute(
                "SELECT title, source_site, relative_path, last_checked, last_updated "
                "FROM series WHERE series_id = 'webtoons.com:1'"
            ).fetchone()
        assert row is not None
        assert row[0] == "T"
        assert row[1] == "webtoons.com"
        assert row[2] == "T"
        assert row[3]
        assert row[4]
        lib.close()

    def test_upsert_chapter_is_idempotent(self, tmp_path):
        lib = Library(tmp_path / "lib.db")
        lib.open()
        lib.upsert_series("s", title="S")
        lib.upsert_chapter("s", url="https://x/1/", cbz="C.cbz", size_bytes=10)
        lib.upsert_chapter("s", url="https://x/1/", cbz="C.cbz", size_bytes=20)
        with sqlite3.connect(str(tmp_path / "lib.db")) as conn:
            rows = conn.execute(
                "SELECT url, size_bytes FROM chapters WHERE series_id = 's'"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == normalize_url("https://x/1/")
        assert rows[0][1] == 20
        lib.close()

    def test_normalizes_url_on_insert(self, tmp_path):
        lib = Library(tmp_path / "lib.db")
        lib.open()
        lib.upsert_series("s", title="S")
        lib.upsert_chapter("s", url="https://EXAMPLE.com/Path/", cbz="C.cbz")
        with sqlite3.connect(str(tmp_path / "lib.db")) as conn:
            stored = conn.execute(
                "SELECT url FROM chapters WHERE series_id = 's'"
            ).fetchone()[0]
        assert stored == normalize_url("https://EXAMPLE.com/Path/")
        assert stored == "https://example.com/Path"
        lib.close()

    def test_chapter_requires_series_row(self, tmp_path):
        from comic_dl.errors import LibraryError

        lib = Library(tmp_path / "lib.db")
        lib.open()
        with pytest.raises(LibraryError):
            lib.upsert_chapter("missing-series", url="https://x/1/", cbz="C.cbz")
        with sqlite3.connect(str(tmp_path / "lib.db")) as conn:
            count = conn.execute("SELECT COUNT(*) FROM chapters").fetchone()[0]
        assert count == 0
        lib.close()

    def test_empty_url_is_ignored(self, tmp_path):
        lib = Library(tmp_path / "lib.db")
        lib.open()
        lib.upsert_series("s", title="S")
        lib.upsert_chapter("s", url="", cbz="C.cbz")
        with sqlite3.connect(str(tmp_path / "lib.db")) as conn:
            count = conn.execute("SELECT COUNT(*) FROM chapters").fetchone()[0]
        assert count == 0
        lib.close()

    def test_writes_silent_when_db_unavailable(self, tmp_path):
        db = tmp_path / "lib.db"
        db.write_bytes(b"garbage")
        lib = Library(db)
        lib.open()
        assert not lib.available
        lib.upsert_series("s", title="S")
        lib.upsert_chapter("s", url="https://x/1/", cbz="C.cbz")
        lib.set_last_checked("s")
        lib.set_last_updated("s")
        assert lib.build_have_set("s", tmp_path, []) == set()
        lib.close()


class TestBuildHaveSet:
    def _lib(self, tmp_path, db: str = "lib.db") -> Library:
        lib = Library(tmp_path / db)
        lib.open()
        return lib

    def test_db_rows_with_existing_file(self, tmp_path):
        series_dir = tmp_path / "Series"
        series_dir.mkdir()
        lib = self._lib(tmp_path)
        lib.upsert_series("s:1", title="S")
        lib.upsert_chapter("s:1", url="https://x/1/", cbz="Chapter 1.cbz", size_bytes=5)
        _make_cbz(series_dir / "Chapter 1.cbz", web="https://x/1/")
        chapters = [{"title": "Chapter 1", "episode_no": "1", "url": "https://x/1/"}]
        assert "https://x/1" in lib.build_have_set("s:1", series_dir, chapters)
        lib.close()

    def test_db_row_ignored_when_file_missing(self, tmp_path):
        series_dir = tmp_path / "Series"
        series_dir.mkdir()
        lib = self._lib(tmp_path)
        lib.upsert_series("s:1", title="S")
        lib.upsert_chapter("s:1", url="https://x/1/", cbz="Chapter 1.cbz")
        chapters = [{"title": "Chapter 1", "episode_no": "1", "url": "https://x/1/"}]
        assert lib.build_have_set("s:1", series_dir, chapters) == set()
        lib.close()

    def test_scan_embedded_url(self, tmp_path):
        series_dir = tmp_path / "Series"
        series_dir.mkdir()
        _make_cbz(series_dir / "Chapter 1.cbz", web="https://x/1/")
        lib = self._lib(tmp_path)
        chapters = [{"title": "Chapter 1", "episode_no": "1", "url": "https://x/1/"}]
        assert lib.build_have_set("s:1", series_dir, chapters) == {"https://x/1"}
        lib.close()

    def test_filename_fallback_without_embedded_url(self, tmp_path):
        series_dir = tmp_path / "Series"
        series_dir.mkdir()
        _make_cbz(series_dir / "Chapter 1.cbz", web="")
        lib = self._lib(tmp_path)
        chapters = [{"title": "Chapter 1", "episode_no": "1", "url": "https://x/1/"}]
        assert lib.build_have_set("s:1", series_dir, chapters) == {"https://x/1"}
        lib.close()

    def test_filename_fallback_disambiguated(self, tmp_path):
        series_dir = tmp_path / "Series"
        series_dir.mkdir()
        _make_cbz(series_dir / "Chapter 1 (42).cbz", web="")
        lib = self._lib(tmp_path)
        chapters = [{"title": "Chapter 1", "episode_no": "1", "url": "https://x/1/"}]
        assert lib.build_have_set("s:1", series_dir, chapters) == {"https://x/1"}
        lib.close()

    def test_filename_fallback_uses_episode_label(self, tmp_path):
        series_dir = tmp_path / "Series"
        series_dir.mkdir()
        _make_cbz(series_dir / "Ep. 3.cbz", web="")
        lib = self._lib(tmp_path)
        chapters = [{"title": "", "episode_no": "3", "url": "https://x/3/"}]
        assert lib.build_have_set("s:1", series_dir, chapters) == {"https://x/3"}
        lib.close()

    def test_zip_embedded_url_found(self, tmp_path):
        series_dir = tmp_path / "Series"
        series_dir.mkdir()
        _make_cbz(series_dir / "Chapter 1.zip", web="https://x/1/")
        lib = self._lib(tmp_path)
        chapters = [{"title": "Chapter 1", "episode_no": "1", "url": "https://x/1/"}]
        assert lib.build_have_set("s:1", series_dir, chapters) == {"https://x/1"}
        lib.close()

    def test_cbt_embedded_url_found(self, tmp_path):
        series_dir = tmp_path / "Series"
        series_dir.mkdir()
        _make_cbt(series_dir / "Chapter 1.cbt", web="https://x/1/")
        lib = self._lib(tmp_path)
        chapters = [{"title": "Chapter 1", "episode_no": "1", "url": "https://x/1/"}]
        assert lib.build_have_set("s:1", series_dir, chapters) == {"https://x/1"}
        lib.close()

    def test_cbt_filename_fallback(self, tmp_path):
        series_dir = tmp_path / "Series"
        series_dir.mkdir()
        _make_cbt(series_dir / "Chapter 1.cbt", web="")
        lib = self._lib(tmp_path)
        chapters = [{"title": "Chapter 1", "episode_no": "1", "url": "https://x/1/"}]
        assert lib.build_have_set("s:1", series_dir, chapters) == {"https://x/1"}
        lib.close()

    def test_zip_filename_fallback_disambiguated(self, tmp_path):
        series_dir = tmp_path / "Series"
        series_dir.mkdir()
        _make_cbz(series_dir / "Chapter 1 (42).zip", web="")
        lib = self._lib(tmp_path)
        chapters = [{"title": "Chapter 1", "episode_no": "1", "url": "https://x/1/"}]
        assert lib.build_have_set("s:1", series_dir, chapters) == {"https://x/1"}
        lib.close()

    def test_partial_marker_excludes_embedded_url(self, tmp_path):
        series_dir = tmp_path / "Series"
        series_dir.mkdir()
        _make_cbz(series_dir / "Chapter 1.cbz", web="https://x/1/")
        (series_dir / "Chapter 1.cbz.partial").write_bytes(b"")
        lib = self._lib(tmp_path)
        chapters = [{"title": "Chapter 1", "episode_no": "1", "url": "https://x/1/"}]
        assert lib.build_have_set("s:1", series_dir, chapters) == set()
        lib.close()

    def test_partial_marker_excludes_filename_fallback(self, tmp_path):
        series_dir = tmp_path / "Series"
        series_dir.mkdir()
        _make_cbz(series_dir / "Chapter 1.cbz", web="")
        (series_dir / "Chapter 1.cbz.partial").write_bytes(b"")
        lib = self._lib(tmp_path)
        chapters = [{"title": "Chapter 1", "episode_no": "1", "url": "https://x/1/"}]
        assert lib.build_have_set("s:1", series_dir, chapters) == set()
        lib.close()

    def test_partial_marker_excludes_db_row(self, tmp_path):
        series_dir = tmp_path / "Series"
        series_dir.mkdir()
        lib = self._lib(tmp_path)
        lib.upsert_series("s:1", title="S")
        lib.upsert_chapter("s:1", url="https://x/1/", cbz="Chapter 1.cbz")
        _make_cbz(series_dir / "Chapter 1.cbz", web="https://x/1/")
        (series_dir / "Chapter 1.cbz.partial").write_bytes(b"")
        chapters = [{"title": "Chapter 1", "episode_no": "1", "url": "https://x/1/"}]
        assert lib.build_have_set("s:1", series_dir, chapters) == set()
        lib.close()

    def test_complete_sibling_not_affected_by_other_partial(self, tmp_path):
        series_dir = tmp_path / "Series"
        series_dir.mkdir()
        _make_cbz(series_dir / "Chapter 1.cbz", web="https://x/1/")
        _make_cbz(series_dir / "Chapter 2.cbz", web="https://x/2/")
        (series_dir / "Chapter 2.cbz.partial").write_bytes(b"")
        lib = self._lib(tmp_path)
        chapters = [
            {"title": "Chapter 1", "episode_no": "1", "url": "https://x/1/"},
            {"title": "Chapter 2", "episode_no": "2", "url": "https://x/2/"},
        ]
        assert lib.build_have_set("s:1", series_dir, chapters) == {"https://x/1"}
        lib.close()

    def test_corrupt_cbz_is_ignored(self, tmp_path):
        series_dir = tmp_path / "Series"
        series_dir.mkdir()
        (series_dir / "Other.cbz").write_bytes(b"not a zip")
        lib = self._lib(tmp_path)
        chapters = [{"title": "Chapter 1", "episode_no": "1", "url": "https://x/1/"}]
        assert lib.build_have_set("s:1", series_dir, chapters) == set()
        lib.close()

    def test_normalization_matches_different_forms(self, tmp_path):
        series_dir = tmp_path / "Series"
        series_dir.mkdir()
        _make_cbz(series_dir / "Chapter 1.cbz", web="https://x/1/")
        lib = self._lib(tmp_path)
        chapters = [{"title": "Chapter 1", "episode_no": "1", "url": "https://x/1"}]
        assert "https://x/1" in lib.build_have_set("s:1", series_dir, chapters)
        lib.close()

    def test_scan_only_when_db_unavailable(self, tmp_path):
        series_dir = tmp_path / "Series"
        series_dir.mkdir()
        _make_cbz(series_dir / "Chapter 1.cbz", web="https://x/1/")
        db = tmp_path / "lib.db"
        db.write_bytes(b"not a sqlite database")
        lib = Library(db)
        lib.open()
        assert not lib.available
        chapters = [{"title": "Chapter 1", "episode_no": "1", "url": "https://x/1/"}]
        assert lib.build_have_set("s:1", series_dir, chapters) == {"https://x/1"}
        lib.close()

    def test_missing_series_dir(self, tmp_path):
        lib = self._lib(tmp_path)
        assert lib.build_have_set("s:1", tmp_path / "DoesNotExist", []) == set()
        lib.close()


class TestReadMethods:
    def _lib(self, tmp_path, db: str = "lib.db") -> Library:
        lib = Library(tmp_path / db)
        lib.open()
        return lib

    def _seed(self, lib: Library) -> None:
        for _i, (sid, title, site) in enumerate(
            [
                ("e-hentai.org:aaa", "Alpha", "e-hentai.org"),
                ("e-hentai.org:bbb", "Beta", "e-hentai.org"),
                ("webtoons.com:ccc", "Gamma", "webtoons.com"),
            ]
        ):
            lib.upsert_series(
                sid, title=title, source="https://x/",
                source_site=site, relative_path=title,
            )
        lib.upsert_chapter(
            "e-hentai.org:aaa", url="https://e-hentai.org/g/aaa/1/",
            chapter_no="1", title="Ch 1", cbz="1.cbz", size_bytes=100, page_count=5,
        )
        lib.upsert_chapter(
            "e-hentai.org:aaa", url="https://e-hentai.org/g/aaa/2/",
            chapter_no="2", title="Ch 2", cbz="2.cbz", size_bytes=200, page_count=6,
        )
        lib.upsert_chapter(
            "webtoons.com:ccc", url="https://webtoons.com/ep/3",
            chapter_no="3", title="Ch 3", cbz="3.cbz", size_bytes=300, page_count=7,
        )

    def test_list_series_counts_and_sizes(self, tmp_path):
        lib = self._lib(tmp_path)
        self._seed(lib)
        rows = lib.list_series()
        by_id = {r["series_id"]: r for r in rows}
        assert by_id["e-hentai.org:aaa"]["chapter_count"] == 2
        assert by_id["e-hentai.org:aaa"]["total_size"] == 300
        assert by_id["webtoons.com:ccc"]["chapter_count"] == 1
        assert by_id["webtoons.com:ccc"]["total_size"] == 300
        assert by_id["e-hentai.org:bbb"]["chapter_count"] == 0
        assert by_id["e-hentai.org:bbb"]["total_size"] == 0
        lib.close()

    def test_list_series_empty(self, tmp_path):
        lib = self._lib(tmp_path)
        assert lib.list_series() == []
        lib.close()

    def test_find_series_by_series_id(self, tmp_path):
        lib = self._lib(tmp_path)
        self._seed(lib)
        match = lib.find_series("webtoons.com:ccc")
        assert len(match) == 1
        assert match[0]["title"] == "Gamma"
        lib.close()

    def test_find_series_by_title_case_insensitive(self, tmp_path):
        lib = self._lib(tmp_path)
        self._seed(lib)
        assert lib.find_series("beta")[0]["title"] == "Beta"
        lib.close()

    def test_find_series_by_substring(self, tmp_path):
        lib = self._lib(tmp_path)
        self._seed(lib)
        assert lib.find_series("alph")[0]["series_id"] == "e-hentai.org:aaa"
        lib.close()

    def test_find_series_substring_may_match_many(self, tmp_path):
        lib = self._lib(tmp_path)
        self._seed(lib)
        matches = lib.find_series("a")
        titles = {m["title"] for m in matches}
        assert titles == {"Alpha", "Beta", "Gamma"}
        assert len(matches) == 3
        lib.close()

    def test_find_series_no_match(self, tmp_path):
        lib = self._lib(tmp_path)
        self._seed(lib)
        assert lib.find_series("nope") == []
        lib.close()

    def test_find_series_by_source_url(self, tmp_path):
        lib = self._lib(tmp_path)
        lib.upsert_series(
            "webtoons.com:10482", title="Lodoss",
            source=normalize_url(
                "https://www.webtoons.com/en/action/list?title_no=10482"
            ),
            source_site="webtoons.com", relative_path="Lodoss",
        )
        # A trailing slash on the path (but not the query) is normalized away.
        match = lib.find_series(
            "https://www.webtoons.com/en/action/list/?title_no=10482"
        )
        assert len(match) == 1
        assert match[0]["series_id"] == "webtoons.com:10482"
        lib.close()

    def test_find_series_url_no_match(self, tmp_path):
        lib = self._lib(tmp_path)
        self._seed(lib)
        assert lib.find_series("https://example.com/not-a-series") == []
        lib.close()

    def test_find_series_empty_query(self, tmp_path):
        lib = self._lib(tmp_path)
        self._seed(lib)
        assert lib.find_series("") == []
        lib.close()

    def test_get_series(self, tmp_path):
        lib = self._lib(tmp_path)
        self._seed(lib)
        row = lib.get_series("e-hentai.org:aaa")
        assert row is not None
        assert row["title"] == "Alpha"
        assert lib.get_series("missing") is None
        lib.close()

    def test_get_chapters_returns_all(self, tmp_path):
        lib = self._lib(tmp_path)
        self._seed(lib)
        rows = lib.get_chapters("e-hentai.org:aaa")
        assert len(rows) == 2
        urls = {r["url"] for r in rows}
        assert urls == {
            normalize_url("https://e-hentai.org/g/aaa/1/"),
            normalize_url("https://e-hentai.org/g/aaa/2/"),
        }
        assert lib.get_chapters("missing") == []
        lib.close()

    def test_chapters_since_filters_by_iso_cutoff(self, tmp_path):
        lib = self._lib(tmp_path)
        self._seed(lib)
        with sqlite3.connect(str(tmp_path / "lib.db")) as conn:
            conn.execute(
                "UPDATE chapters SET downloaded_at = '2026-01-01T00:00:00'"
            )
        later = lib.chapters_since("2025-12-31T23:59:59")
        assert len(later) == 3
        none = lib.chapters_since("2026-01-01T00:00:01")
        assert none == []
        lib.close()

    def test_chapters_since_joins_series_title(self, tmp_path):
        lib = self._lib(tmp_path)
        self._seed(lib)
        rows = lib.chapters_since("2000-01-01T00:00:00")
        titles = {r["series_title"] for r in rows}
        assert titles == {"Alpha", "Gamma"}
        newest = max(r["downloaded_at"] for r in rows)
        assert rows[0]["downloaded_at"] == newest
        lib.close()

    def test_remove_series_cascades(self, tmp_path):
        lib = self._lib(tmp_path)
        self._seed(lib)
        assert lib.remove_series("e-hentai.org:aaa")
        assert lib.get_series("e-hentai.org:aaa") is None
        assert lib.get_chapters("e-hentai.org:aaa") == []
        assert lib.get_series("webtoons.com:ccc") is not None
        lib.close()

    def test_remove_series_returns_false_when_missing(self, tmp_path):
        lib = self._lib(tmp_path)
        self._seed(lib)
        assert lib.remove_series("missing") is False
        lib.close()


class TestSchemaMigration:
    def test_v1_schema_gains_downloads_table(self, tmp_path):
        db = tmp_path / "library.db"
        with sqlite3.connect(str(db)) as conn:
            conn.execute("PRAGMA user_version = 1")
            conn.execute(
                """CREATE TABLE series (
                    series_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL, source TEXT, source_site TEXT,
                    relative_path TEXT NOT NULL, last_checked TEXT,
                    last_updated TEXT, created_at TEXT)"""
            )
            conn.execute(
                """CREATE TABLE chapters (
                    series_id TEXT NOT NULL, url TEXT NOT NULL,
                    chapter_id TEXT, chapter_no TEXT, title TEXT,
                    cbz TEXT NOT NULL, size_bytes INTEGER, page_count INTEGER,
                    downloaded_at TEXT,
                    PRIMARY KEY (series_id, url))"""
            )
        lib = Library(db)
        lib.open()
        assert lib.available
        with sqlite3.connect(str(db)) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "downloads" in tables
            cols = {
                r[1]
                for r in conn.execute("PRAGMA table_info(series)").fetchall()
            }
            assert {"source_host", "source_id"} <= cols
        lib.close()

    def test_v2_schema_gains_source_host_and_backfills(self, tmp_path):
        db = tmp_path / "library.db"
        with sqlite3.connect(str(db)) as conn:
            conn.execute("PRAGMA user_version = 2")
            conn.execute(
                """CREATE TABLE series (
                    series_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL, source TEXT, source_site TEXT,
                    relative_path TEXT NOT NULL, last_checked TEXT,
                    last_updated TEXT, created_at TEXT)"""
            )
            conn.execute(
                """CREATE TABLE chapters (
                    series_id TEXT NOT NULL, url TEXT NOT NULL,
                    chapter_id TEXT, chapter_no TEXT, title TEXT,
                    cbz TEXT NOT NULL, size_bytes INTEGER, page_count INTEGER,
                    downloaded_at TEXT,
                    PRIMARY KEY (series_id, url))"""
            )
            conn.execute(
                """CREATE TABLE downloads (
                    url TEXT PRIMARY KEY, path TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'cbz')"""
            )
            conn.execute(
                """INSERT INTO series (series_id, title, source, relative_path)
                   VALUES ('s:1', 'Alpha', 'https://E-HENTAI.org/g/x/1', 'Alpha')"""
            )
        lib = Library(db)
        lib.open()
        assert lib.available
        row = lib.get_series("s:1")
        assert row is not None
        assert row["source_host"] == "e-hentai.org"
        assert row["source_id"] is None
        with sqlite3.connect(str(db)) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        lib.close()

    def test_upsert_series_fills_source_host_and_id(self, tmp_path):
        lib = Library(tmp_path / "library.db")
        lib.open()
        lib.upsert_series(
            "e-hentai.org:aaa",
            title="Alpha",
            source="https://e-hentai.org/g/x/1",
            source_site="e-hentai.org",
            relative_path="Alpha",
            source_id=source_id("e-hentai", "e-hentai.org", "1.0"),
        )
        row = lib.get_series("e-hentai.org:aaa")
        assert row is not None
        assert row["source_host"] == "e-hentai.org"
        assert row["source_id"] == source_id("e-hentai", "e-hentai.org", "1.0")
        lib.close()


class TestUrlHelpers:
    def test_url_host_lowercases(self):
        assert url_host("https://E-Hentai.org/g/x/1") == "e-hentai.org"

    def test_url_host_unparsable(self):
        assert url_host("") == ""
        assert url_host("not a url") == ""

    def test_url_identity_splits_host_path(self):
        host, path = url_identity("https://FSIComics.com/series/ch-1/?x=1")
        assert host == "fsicomics.com"
        assert path == "/series/ch-1/?x=1"

    def test_rebase_url_swaps_host_keeps_path(self):
        rebased = rebase_url(
            "https://old.fsicomics.com/series/ch-1/?x=1", "new.fsicomics.com"
        )
        assert rebased.startswith("https://new.fsicomics.com/series/ch-1/?x=1")

    def test_rebase_url_empty(self):
        assert rebase_url("", "x.com") == ""

    def test_source_id_stable_and_distinct(self):
        a = source_id("FSIComics", "fsicomics.com", "1.0")
        assert a == source_id("fsicomics", "FSICOMICS.COM", "1.0")
        assert len(a) == 16
        assert a != source_id("FSIComics", "fsicomics.com", "2.0")
        assert a != source_id("Other", "fsicomics.com", "1.0")


class TestStandaloneDownloads:
    def _open(self, tmp_path) -> Library:
        lib = Library(library_path(tmp_path))
        lib.open()
        return lib

    def test_upsert_download_round_trip_normalizes(self, tmp_path):
        lib = self._open(tmp_path)
        lib.upsert_download(
            "https://e-hentai.org/g/a/1/", "Series/Ep 1.cbz", "cbz"
        )
        lib.upsert_download(
            "https://e-hentai.org/g/a/1/", "Series/Ep 1.cbz", "cbz"
        )
        (tmp_path / "Series").mkdir(parents=True, exist_ok=True)
        (tmp_path / "Series" / "Ep 1.cbz").write_bytes(b"\x00")
        with sqlite3.connect(str(library_path(tmp_path))) as conn:
            rows = conn.execute("SELECT url, path, kind FROM downloads").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == normalize_url("https://e-hentai.org/g/a/1")
        lib.close()

    def test_downloaded_index_joins_series_relative_path(self, tmp_path):
        lib = self._open(tmp_path)
        sdir = tmp_path / "Alpha"
        sdir.mkdir()
        _make_cbz(sdir / "1.cbz")
        lib.upsert_series(
            "s:1", title="Alpha", source="https://x/", relative_path="Alpha"
        )
        lib.upsert_chapter("s:1", url="https://x/ep/1", cbz="1.cbz", title="Ch 1")
        lib.upsert_download(
            "https://pawchive.pw/u/1/post/2", "Alpha/note.md", "md"
        )
        (sdir / "note.md").write_text(
            "# n\n", encoding="utf-8"
        )
        index = lib.downloaded_index(tmp_path)
        assert index[normalize_url("https://x/ep/1")] == sdir / "1.cbz"
        assert index[normalize_url("https://pawchive.pw/u/1/post/2")] == sdir / "note.md"
        lib.close()

    def test_downloaded_index_drops_missing_files(self, tmp_path):
        lib = self._open(tmp_path)
        sdir = tmp_path / "Alpha"
        sdir.mkdir()
        lib.upsert_series(
            "s:1", title="Alpha", source="https://x/", relative_path="Alpha"
        )
        lib.upsert_chapter("s:1", url="https://x/ep/1", cbz="gone.cbz")
        lib.upsert_download("https://x/solo", "Alpha/solo.cbz", "cbz")
        assert lib.downloaded_index(tmp_path) == {}
        lib.close()


class TestThreadedAccess:
    """One shared connection must tolerate concurrent threads (F14)."""

    def test_concurrent_writes_and_reads_do_not_interleave(self, tmp_path):
        import threading

        lib = Library(library_path(tmp_path))
        lib.open()
        errors: list[Exception] = []

        def writer(n: int) -> None:
            try:
                for i in range(20):
                    sid = f"s:{n}:{i}"
                    lib.upsert_series(sid, title=f"S{n}-{i}")
                    lib.upsert_chapter(sid, url=f"https://x/{n}/{i}", cbz=f"{i}.cbz")
                    lib.set_last_checked(sid)
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        def reader() -> None:
            try:
                for _ in range(50):
                    lib.list_series()
                    lib.chapters_since("0000-00-00")
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [
            threading.Thread(target=writer, args=(n,))
            for n in range(4)
        ] + [threading.Thread(target=reader) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == []
        # Every write from every writer thread landed.
        rows = lib.list_series()
        assert len(rows) == 4 * 20
        for n in range(4):
            chapters = lib.get_chapters(f"s:{n}:19")
            assert len(chapters) == 1
        lib.close()
