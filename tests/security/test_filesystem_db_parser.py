"""Filesystem, database, parser, and concurrency security regression tests."""

from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path

import pytest

from comic_dl import utils
from comic_dl.cli import MAX_CONCURRENCY, parse_urls
from comic_dl.downloader import download_httpx
from comic_dl.library import Library
from comic_dl.models import ImageItem

# ---------------------------------------------------------------------------
# Path traversal / filesystem containment
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    [
        "../../../evil",
        "/etc/passwd",
        "C:\\windows\\system32",
        "..",
        "CON",
        "aux",
        "a/b",
        "..\\..\\escape",
    ],
)
def test_sanitize_filename_never_absolute_or_parent(name: str) -> None:
    out = utils.sanitize_filename(name)
    assert not Path(out).is_absolute()
    assert ".." not in Path(out).parts


class _OkResponse:
    status_code = 200
    headers = {"content-length": "3"}

    def raise_for_status(self):
        pass

    async def aiter_content(self, chunk_size=None):
        yield b"\xff\xd8\xff"


class _CloneClient:
    def stream(self, method, url, **kwargs):
        return _OkResponse()


@pytest.mark.asyncio
async def test_download_cannot_escape_host(tmp_path) -> None:
    dest = tmp_path / "out"
    dest.mkdir()
    evil = ImageItem(
        url="http://example.com/a.jpg",
        page_number=1,
        filename="../../escape.jpg",
    )
    failed = await download_httpx([evil], dest, concurrency=1, client=_CloneClient())
    assert "../escape.jpg" in failed or "../../escape.jpg" in failed
    assert not (tmp_path / "escape.jpg").exists()


# ---------------------------------------------------------------------------
# SQLite parameterisation, LIKE escaping, migration safety
# ---------------------------------------------------------------------------

def _library(tmp_path: Path) -> Library:
    lib = Library(tmp_path / "library.db")
    lib.open()
    return lib


def test_sql_parameterised_and_literal(tmp_path) -> None:
    lib = _library(tmp_path)
    lib.upsert_series(
        "s1", title="Good ' OR 1=1 --", source="http://x.com", relative_path="r"
    )
    lib.upsert_series("s2", title="Plain", source="http://x", relative_path="r2")
    assert lib.find_series("zzz-nonexistent") == []
    assert lib.find_series("Good ' OR 1=1 --")[0]["series_id"] == "s1"
    lib.close()


def test_like_metacharacters_are_bounded_no_injection(tmp_path) -> None:
    lib = _library(tmp_path)
    lib.upsert_series("s1", title="100% real", source="http://x", relative_path="r")
    lib.upsert_series("s2", title="Plain", source="http://x", relative_path="r2")
    # Quote/wildcard-laden queries are bound as data: never an error, never a
    # full-row dump via injection.
    assert lib.find_series("' OR '1'='1") == []
    hits = lib.find_series("%")
    assert isinstance(hits, list)
    assert all("series_id" in h for h in hits)
    lib.close()


def test_future_schema_refused_not_corrupted(tmp_path) -> None:
    db = tmp_path / "library.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version = 999")
    conn.execute("CREATE TABLE series (series_id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    lib = Library(db)
    lib.open()
    assert not lib.available
    lib.close()


def test_fresh_schema_roundtrips(tmp_path) -> None:
    lib = _library(tmp_path)
    assert lib.available
    lib.upsert_series("s1", title="T", source="http://x", relative_path="r")
    lib.upsert_chapter("s1", url="http://x/ch", title="C1", cbz="c1.cbz")
    assert len(lib.get_chapters("s1")) == 1
    lib.close()


# ---------------------------------------------------------------------------
# Untrusted .cbz ComicInfo.xml must not expand entities or reach the disk
# ---------------------------------------------------------------------------

def _cbz(tmp_path: Path, xml: bytes) -> Path:
    p = tmp_path / "book.cbz"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("ComicInfo.xml", xml)
    return p


def test_xxe_in_cbz_xml_refused(tmp_path) -> None:
    hostile = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE ComicInfo [<!ENTITY file SYSTEM "file:///etc/hostname">]>'
        b"<ComicInfo><Web>&file;</Web></ComicInfo>"
    )
    assert utils.cbz_source_url(_cbz(tmp_path, hostile)) == ""


def test_entity_bomb_in_cbz_xml_rejected(tmp_path) -> None:
    hostile = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE a [<!ENTITY b "123"><!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]>'
        b"<a><x>&c;</x></a>"
    )
    assert utils.cbz_source_url(_cbz(tmp_path, hostile)) == ""


def test_benign_cbz_source_url_parses(tmp_path) -> None:
    good = b'<?xml version="1.0"?><ComicInfo><Web>http://example.com/g</Web></ComicInfo>'
    assert utils.cbz_source_url(_cbz(tmp_path, good)) == "http://example.com/g"


# ---------------------------------------------------------------------------
# Concurrency bounds
# ---------------------------------------------------------------------------

def test_concurrency_capped_at_max(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["comic-dl", "--concurrency", "999999", "--url",
         "https://example.com", "-o", "/tmp/out"],
    )
    _, args = parse_urls()
    assert args.concurrency == MAX_CONCURRENCY


def test_concurrency_below_one_is_an_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["comic-dl", "--concurrency", "0", "--url",
         "https://example.com", "-o", "/tmp/out"],
    )
    with pytest.raises(SystemExit):
        parse_urls()
