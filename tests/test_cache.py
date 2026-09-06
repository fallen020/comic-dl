"""Tests for the on-disk scrape response cache (comic_dl.cache)."""

from __future__ import annotations

import json
import os
import struct
import time

import pytest
from curl_cffi.requests.exceptions import HTTPError

from comic_dl import cache, config


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path):
    cache.set_cache_dir(tmp_path / "http")
    yield
    cache.set_cache_dir(None)


URL = "https://kagane.to/series/foo"
PROFILE = "chrome146"


def _store(url=URL, body=b'{"title": "hi"}', status=200, headers=None):
    cache.store(
        url,
        profile=PROFILE,
        extra_headers={},
        status=status,
        headers=headers or {"Content-Type": "text/html; charset=utf-8"},
        body=body,
    )


def _make_stale(url=URL):
    _store(url)
    path = cache._entry_path(url, PROFILE, {})
    entry = cache._read_entry(path)
    entry["created"] = time.time() - cache.cache_ttl_hours() * 3600 - 1
    cache._write_entry(path, entry)


def test_store_lookup_roundtrip():
    _store()
    resp, stale = cache.lookup(URL, PROFILE, {})
    assert stale is None
    assert resp is not None
    assert resp.status_code == 200
    assert resp.text == '{"title": "hi"}'
    assert resp.content == b'{"title": "hi"}'
    assert resp.json() == {"title": "hi"}


def test_miss_returns_none():
    resp, stale = cache.lookup("https://kagane.to/series/nope", PROFILE, {})
    assert resp is None and stale is None


def test_stale_entry_returned_for_revalidation():
    _make_stale()
    resp, stale = cache.lookup(URL, PROFILE, {})
    assert resp is None
    assert stale is not None
    assert stale["body"] == b'{"title": "hi"}'


def test_entry_exactly_at_ttl_is_stale():
    """Freshness is a strict ``< TTL`` window: an entry created exactly TTL
    ago must be handed back for conditional revalidation, never served warm."""
    _store()
    path = cache._entry_path(URL, PROFILE, {})
    entry = cache._read_entry(path)
    entry["created"] = time.time() - cache.cache_ttl_hours() * 3600
    cache._write_entry(path, entry)
    resp, stale = cache.lookup(URL, PROFILE, {})
    assert resp is None and stale is not None


def test_entry_within_ttl_is_fresh():
    """An entry created just inside the TTL window is still served warm."""
    _store()
    path = cache._entry_path(URL, PROFILE, {})
    entry = cache._read_entry(path)
    entry["created"] = time.time() - cache.cache_ttl_hours() * 3600 + 60
    cache._write_entry(path, entry)
    resp, stale = cache.lookup(URL, PROFILE, {})
    assert resp is not None and stale is None


def test_conditional_headers_include_validators():
    _store()
    path = cache._entry_path(URL, PROFILE, {})
    entry = cache._read_entry(path)
    entry["etag"] = '"abc"'
    entry["last_modified"] = "Mon, 01 Jan 2024 00:00:00 GMT"
    cache._write_entry(path, entry)
    headers = cache.conditional_headers(entry)
    assert headers == {
        "If-None-Match": '"abc"',
        "If-Modified-Since": "Mon, 01 Jan 2024 00:00:00 GMT",
    }


def test_refresh_extends_ttl_keeps_body():
    _make_stale()
    _, stale = cache.lookup(URL, PROFILE, {})
    assert stale is not None
    cache.refresh(URL, PROFILE, {}, stale)
    resp, stale = cache.lookup(URL, PROFILE, {})
    assert resp is not None and stale is None
    assert resp.content == b'{"title": "hi"}'


def test_key_differs_by_url_profile_and_headers():
    p1 = cache._entry_path(URL, PROFILE, {})
    p2 = cache._entry_path(URL, "chrome116", {})
    p3 = cache._entry_path(URL, PROFILE, {"Referer": "https://kagane.to"})
    p4 = cache._entry_path("https://kagane.to/series/bar", PROFILE, {})
    assert len({p1, p2, p3, p4}) == 4


def test_store_skips_set_cookie():
    cache.store(
        URL,
        profile=PROFILE,
        extra_headers={},
        status=200,
        headers={"Set-Cookie": "cf_clearance=abc"},
        body=b"x",
    )
    resp, stale = cache.lookup(URL, PROFILE, {})
    assert resp is None and stale is None


def test_clear_removes_entries():
    _store()
    _store("https://kagane.to/series/bar", body=b"x")
    assert cache.clear() == 2
    resp, stale = cache.lookup(URL, PROFILE, {})
    assert resp is None and stale is None


def test_corrupt_entry_ignored():
    target = cache._entry_path(URL, PROFILE, {})
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"not-a-cache-file")
    resp, stale = cache.lookup(URL, PROFILE, {})
    assert resp is None and stale is None


def test_store_refuses_non_2xx():
    _store(status=500, body=b"oops")
    resp, stale = cache.lookup(URL, PROFILE, {})
    assert resp is None and stale is None


def test_store_normalizes_list_headers():
    cache.store(
        URL,
        profile=PROFILE,
        extra_headers={},
        status=200,
        headers={"Vary": ["Accept-Encoding", "User-Agent"]},
        body=b"x",
    )
    resp, _ = cache.lookup(URL, PROFILE, {})
    assert resp is not None
    assert resp.headers["Vary"] == "Accept-Encoding, User-Agent"


def test_read_rejects_truncated_entry():
    """A file cut short of its advertised metadata length is not served."""
    path = cache._entry_path(URL, PROFILE, {})
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = cache._MAGIC + struct.pack(">I", cache._VERSION) + struct.pack(">I", 1000) + b"x"
    path.write_bytes(payload)
    resp, stale = cache.lookup(URL, PROFILE, {})
    assert resp is None and stale is None


def test_clear_removes_orphan_tmp():
    """Interrupted writes leave ``.tmp`` files; ``clear`` sweeps them too."""
    _store()
    path = cache._entry_path(URL, PROFILE, {})
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(b"partial")
    assert cache.clear() == 2
    assert not path.exists() and not tmp.exists()


def test_refresh_does_not_mutate_input():
    _make_stale()
    _, stale = cache.lookup(URL, PROFILE, {})
    assert stale is not None
    cache.refresh(URL, PROFILE, {}, stale)
    resp, _ = cache.lookup(URL, PROFILE, {})
    assert resp is not None and stale["created"] != resp._entry["created"]


def test_cached_response_raise_for_status():
    _store(status=204, body=b"")
    resp, _ = cache.lookup(URL, PROFILE, {})
    assert resp is not None
    resp.raise_for_status()
    # 5xx raises even when constructed from a stored entry
    error = cache.CachedResponse({"status": 500, "headers": {}, "body": b"oops"})
    with pytest.raises(HTTPError):
        error.raise_for_status()


def test_cache_enabled_and_ttl_reflect_runtime_config():
    assert cache.cache_enabled() is True
    assert cache.cache_ttl_hours() == 6


def test_cache_max_bytes_default_and_runtime():
    plain = cache.cache_max_bytes()
    assert plain == cache._DEFAULT_MAX_BYTES
    config.set_runtime_http(**{"cache-max-bytes": "3MB"})
    try:
        assert cache.cache_max_bytes() == 3 * 1024 * 1024
    finally:
        config.set_runtime_http(**{"cache-max-bytes": cache._DEFAULT_MAX_BYTES})
    # Plain byte counts work too.
    config.set_runtime_http(**{"cache-max-bytes": 4096})
    try:
        assert cache.cache_max_bytes() == 4096
    finally:
        config.set_runtime_http(**{"cache-max-bytes": cache._DEFAULT_MAX_BYTES})


def test_cache_max_bytes_invalid_falls_back():
    config.set_runtime_http(**{"cache-max-bytes": True})
    try:
        assert cache.cache_max_bytes() == cache._DEFAULT_MAX_BYTES
    finally:
        config.set_runtime_http(**{"cache-max-bytes": cache._DEFAULT_MAX_BYTES})


def test_sweep_removes_overage_entries_below_any_cap(monkeypatch):
    """Entries past the 14-day hard drop age are removed regardless of how many
    entries the cache holds; younger entries survive."""
    monkeypatch.setattr(cache, "_last_sweep", -1.0)
    old = URL
    fresh = "https://kagane.to/series/fresh"
    _store(old)
    _store(fresh)
    now = time.time()
    cutoff = (cache._MAX_ENTRY_AGE_HOURS + 1) * 3600
    path = cache._entry_path(old, PROFILE, {})
    os.utime(path, (now - cutoff, now - cutoff))
    # Re-arm the throttled sweep, then trigger it via a store.
    monkeypatch.setattr(cache, "_last_sweep", -1.0)
    _store("https://kagane.to/series/tick")
    assert not cache._entry_path(old, PROFILE, {}).exists()
    resp, stale = cache.lookup(fresh, PROFILE, {})
    assert resp is not None and stale is None


def test_sweep_evicts_oldest_until_under_size_budget(monkeypatch):
    """Once the cache exceeds the size budget, the oldest entries are evicted
    (oldest first) until it is back under the limit; the newest entries stay."""
    monkeypatch.setattr(cache, "_last_sweep", -1.0)
    config.set_runtime_http(**{"cache-max-bytes": "5KB"})
    try:
        for url in ("https://kagane.to/series/oldest", "https://kagane.to/series/mid",
                    "https://kagane.to/series/newest"):
            _store(url, body=b"x" * 4096)
        # Oldest two have highest mtime (all three stored; make mtimes explicit).
        now = time.time()
        for idx, url in enumerate(("https://kagane.to/series/oldest",
                                   "https://kagane.to/series/mid",
                                   "https://kagane.to/series/newest")):
            path = cache._entry_path(url, PROFILE, {})
            os.utime(path, (now - 1000 + idx, now - 1000 + idx))
        monkeypatch.setattr(cache, "_last_sweep", -1.0)
        _store("https://kagane.to/series/tick")
        assert not cache._entry_path("https://kagane.to/series/oldest", PROFILE, {}).exists()
        assert not cache._entry_path("https://kagane.to/series/mid", PROFILE, {}).exists()
        assert cache._entry_path("https://kagane.to/series/newest", PROFILE, {}).exists()
        resp, stale = cache.lookup("https://kagane.to/series/newest", PROFILE, {})
        assert resp is not None and stale is None
    finally:
        config.set_runtime_http(**{"cache-max-bytes": cache._DEFAULT_MAX_BYTES})


def test_sweep_under_budget_keeps_all_entries(monkeypatch):
    """A cache within its size budget is untouched even when it holds many entries."""
    monkeypatch.setattr(cache, "_last_sweep", -1.0)
    config.set_runtime_http(**{"cache-max-bytes": "5KB"})
    try:
        for url in ("https://kagane.to/series/a", "https://kagane.to/series/b"):
            _store(url, body=b"y" * 512)
        monkeypatch.setattr(cache, "_last_sweep", -1.0)
        _store("https://kagane.to/series/c", body=b"z" * 512)
        for url in ("https://kagane.to/series/a", "https://kagane.to/series/b",
                    "https://kagane.to/series/c"):
            assert cache._entry_path(url, PROFILE, {}).exists()
    finally:
        config.set_runtime_http(**{"cache-max-bytes": cache._DEFAULT_MAX_BYTES})


def test_lookup_triggers_sweep(monkeypatch):
    """Reads, not just writes, rearm the GC so a read-only run also cleans up."""
    monkeypatch.setattr(cache, "_last_sweep", -1.0)
    old = URL
    _store(old)
    now = time.time()
    cutoff = (cache._MAX_ENTRY_AGE_HOURS + 1) * 3600
    path = cache._entry_path(old, PROFILE, {})
    entry = cache._read_entry(path)
    entry["created"] = now - cutoff
    cache._write_entry(path, entry)
    os.utime(path, (now - cutoff, now - cutoff))
    # _write_entry ran the throttled sweep already; re-arm so the lookup's own
    # sweep tick is what cleans up.
    monkeypatch.setattr(cache, "_last_sweep", -1.0)
    resp, stale = cache.lookup(old, PROFILE, {})
    assert resp is None and stale is None
    assert not path.exists()


def test_sweep_cleans_orphan_temp_files(monkeypatch, tmp_path):
    """Old temp leftovers are removed even when the entry cap isn't exceeded."""
    monkeypatch.setattr(cache, "_last_sweep", -1.0)
    orphan = tmp_path / "http" / "orphan.tmp"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"partial")
    os.utime(orphan, (1.0, 1.0))
    _store()
    assert not orphan.exists()


def test_corrupt_entry_removed_on_read():
    """A corrupt file is not just ignored: it is removed so it cannot shadow
    the next fetch or accumulate."""
    target = cache._entry_path(URL, PROFILE, {})
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"not-a-cache-file")
    resp, stale = cache.lookup(URL, PROFILE, {})
    assert resp is None and stale is None
    assert not target.exists()


def test_read_rejects_json_list_metadata():
    """Metadata that parses but is not a dict (e.g. a JSON array) is corrupt."""
    meta = json.dumps([1, 2]).encode("utf-8")
    payload = (
        cache._MAGIC
        + struct.pack(">I", cache._VERSION)
        + struct.pack(">I", len(meta))
        + meta
        + b"body"
    )
    path = cache._entry_path(URL, PROFILE, {})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    resp, stale = cache.lookup(URL, PROFILE, {})
    assert resp is None and stale is None
    assert not path.exists()


def test_read_rejects_entry_missing_status():
    """Metadata that parses as a dict but lacks a numeric ``status`` is corrupt."""
    meta = json.dumps({"body": "ignored"}).encode("utf-8")
    payload = (
        cache._MAGIC
        + struct.pack(">I", cache._VERSION)
        + struct.pack(">I", len(meta))
        + meta
        + b"body"
    )
    path = cache._entry_path(URL, PROFILE, {})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    resp, stale = cache.lookup(URL, PROFILE, {})
    assert resp is None and stale is None
    assert not path.exists()


def test_read_rejects_oversized_entry(monkeypatch):
    """An entry past the size cap is dropped without an unbounded read."""
    _store()
    path = cache._entry_path(URL, PROFILE, {})
    monkeypatch.setattr(cache, "_MAX_ENTRY_BYTES", 10)
    resp, stale = cache.lookup(URL, PROFILE, {})
    assert resp is None and stale is None
    assert not path.exists()


def test_overage_entry_removed_on_read():
    _store()
    path = cache._entry_path(URL, PROFILE, {})
    entry = cache._read_entry(path)
    entry["created"] = time.time() - (cache._MAX_ENTRY_AGE_HOURS + 1) * 3600
    cache._write_entry(path, entry)
    resp, stale = cache.lookup(URL, PROFILE, {})
    assert resp is None and stale is None
    assert not path.exists()


def test_invalid_created_dropped_as_overage():
    """A non-numeric ``created`` can't have a meaningful age, so it must not
    survive the over-age check or be served mid-TTL forever."""
    _store()
    path = cache._entry_path(URL, PROFILE, {})
    entry = cache._read_entry(path)
    entry["created"] = "2024-01-01"
    cache._write_entry(path, entry)
    resp, stale = cache.lookup(URL, PROFILE, {})
    assert resp is None and stale is None
    assert not path.exists()


def test_future_created_never_fresh():
    """A ``created`` ahead of the local clock (NTP skew or a planted entry) is
    handed back for revalidation instead of served "fresh" until the clock
    catches up; the body is still preserved as last-known-good."""
    _store()
    path = cache._entry_path(URL, PROFILE, {})
    entry = cache._read_entry(path)
    entry["created"] = time.time() + 3600
    cache._write_entry(path, entry)
    resp, stale = cache.lookup(URL, PROFILE, {})
    assert resp is None
    assert stale is not None and stale["body"] == b'{"title": "hi"}'


def test_lookup_noop_when_cache_disabled():
    _store()
    config.set_runtime_http(cache=False)
    try:
        resp, stale = cache.lookup(URL, PROFILE, {})
        assert resp is None and stale is None
    finally:
        config.set_runtime_http(cache=True)


def test_store_noop_when_cache_disabled():
    config.set_runtime_http(cache=False)
    try:
        _store()
    finally:
        config.set_runtime_http(cache=True)
    resp, stale = cache.lookup(URL, PROFILE, {})
    assert resp is None and stale is None


def test_non_get_methods_bypass_cache():
    _store()
    # Non-GET lookups never see the cache.
    resp, stale = cache.lookup(URL, PROFILE, {}, method="POST")
    assert resp is None and stale is None
    # Non-GET stores are no-ops: nothing lands on disk for a new URL.
    put_url = "https://kagane.to/series/put"
    cache.store(
        put_url,
        profile=PROFILE,
        extra_headers={},
        method="PUT",
        status=200,
        headers={},
        body=b"x",
    )
    resp, stale = cache.lookup(put_url, PROFILE, {})
    assert resp is None and stale is None
    # Non-GET refreshes are no-ops: a stale entry stays stale.
    _make_stale()
    _, stale = cache.lookup(URL, PROFILE, {})
    assert stale is not None
    cache.refresh(URL, PROFILE, {}, stale, method="PATCH")
    _, stale_after = cache.lookup(URL, PROFILE, {})
    assert stale_after is not None
    # Plain GET (case-insensitive) still hits the cache.
    _store()
    resp, stale = cache.lookup(URL, PROFILE, {}, method="get")
    assert resp is not None and stale is None


def test_refresh_ignores_malformed_stale():
    """A stale entry without ``body``/``status`` (or the wrong type) must not
    be written back; ``_write_entry`` would otherwise KeyError on ``body``."""
    cache.refresh(URL, PROFILE, {}, {})
    cache.refresh(URL, PROFILE, {}, {"status": 200})
    cache.refresh(URL, PROFILE, {}, "not-a-dict")
    resp, stale = cache.lookup(URL, PROFILE, {})
    assert resp is None and stale is None


def test_key_normalizes_header_case_and_whitespace():
    """Header-name case and stray key/value whitespace are folded, so configs
    that send the same headers share one entry."""
    a = cache._entry_path(URL, PROFILE, {"Referer": " https://kagane.to "})
    b = cache._entry_path(URL, PROFILE, {"referer": "https://kagane.to "})
    c = cache._entry_path(URL, PROFILE, {"Referer": "https://other.example"})
    assert a == b
    assert a != c


def test_entry_path_rejects_non_https_url():
    with pytest.raises(cache.RequestBlockedError):
        cache._entry_path("ftp://example.com/x", PROFILE, {})


def test_json_handles_utf8_bom_bytes():
    """json() parses the raw bytes so a BOM'd body decodes like a live one."""
    _store(body=b'\xef\xbb\xbf{"title": "hi"}')
    resp, _ = cache.lookup(URL, PROFILE, {})
    assert resp is not None
    assert resp.json() == {"title": "hi"}


def test_write_cleans_temp_on_replace_failure():
    """A failed rename (target exists as a directory) must not leak a temp file."""
    path = cache._entry_path(URL, PROFILE, {})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()
    _store()
    assert list(path.parent.glob("*.tmp")) == []
    resp, stale = cache.lookup(URL, PROFILE, {})
    assert resp is None and stale is None
