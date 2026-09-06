"""Small on-disk HTTP response cache for scrape/metadata GETs.

Repeated runs re-fetch the same pages (chapter lists, series metadata, and
``comic-dl update`` re-scrapes every tracked series). Not re-fetching beats
fetching faster: fresh entries are served with zero network I/O, stale
entries trigger one conditional request (``If-None-Match``/``If-Modified-
Since``) and a ``304`` refreshes the entry for another TTL.

Scope and safety:

- Caches only GETs with no JSON body — the scrape/metadata path. Image bytes
  are never cached here (they flow through the downloader, not this module).
- Cache keys derive from ``http(s)`` URLs that the scrape chokepoint has
  already passed through ``validate_request_url`` (every hop is validated
  before this module is consulted); this module still refuses non-http(s)
  URLs and folds header-name case and stray whitespace so equivalent configs
  share one entry.
- The bounds are enforced here, not just at the call site: lookup, store, and
  refresh short-circuit when the cache is disabled or the request is not a
  GET, so policy cannot drift when a new caller is added.
- The cache is bypassed entirely when ``--no-cache`` or ``--no-cookie`` is
  active, so a run whose cookie semantics differ never serves a stale body.
- Responses that carry ``Set-Cookie`` are never stored.
- Only 2xx responses are stored, so a transient error page can never shadow a
  good body for the rest of the TTL.
- Within the TTL a stale-but-valid 2xx body is served with zero network I/O,
  even if the source is momentarily broken. That is the intended trade-off
  (``comic-dl update`` prefers last-known-good over a transient error), and
  ``--no-cache`` / a fresh run bypass it entirely. An entry whose ``created``
  sits in the future (clock skew, or a planted file) is treated as instantly
  stale instead of "fresh until the clock catches up", so the next run
  revalidates it.

Storage: each entry is a versioned header blob (magic, version, JSON metadata
length) followed by the raw body, written to a unique ``mkstemp`` temp file
and atomically renamed into place — a reader never observes a half-written
entry and concurrent writers for one key cannot truncate each other. Failed
writes remove their temp file. A truncated, corrupt, or oversized entry is
dropped and removed on read rather than served. Removal of corrupt/over-age
entries is best-effort; the worst case for a read/remove race is one
redundant refetch, never data loss (entries are re-fetchable).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import struct
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import cache_dir, http_setting
from .utils import RequestBlockedError, parse_size_string

_MAGIC = b"CDHC"
_VERSION = 1
_DEFAULT_TTL_HOURS = 6
_MAX_ENTRY_AGE_HOURS = 24 * 14
_MAX_ENTRY_BYTES = 64 * 1024 * 1024
_DEFAULT_MAX_BYTES = 50 * 1024 * 1024
_SWEEP_INTERVAL_SECONDS = 3600
_TMP_STALE_SECONDS = 3600

_CACHE_DIR_OVERRIDE: Path | None = None
_last_sweep: float = 0.0


def set_cache_dir(path: Path | None) -> None:
    """Point the cache at ``path`` (tests/embedded runtimes); ``None`` restores default."""
    global _CACHE_DIR_OVERRIDE
    _CACHE_DIR_OVERRIDE = path


class CachedResponse:
    """A network-free stand-in for a scrape response (the subset scrapers use)."""

    def __init__(self, entry: dict[str, Any]) -> None:
        self.status_code = int(entry["status"])
        self.headers = entry.get("headers") or {}
        body = entry["body"]
        self.content = body
        self.text = body.decode("utf-8", errors="replace")
        self._entry = entry

    def raise_for_status(self) -> None:
        # Matches httpx/curl semantics. Only 2xx bodies are ever stored, so a
        # raise here only reflects an entry constructed outside this module.
        if 400 <= self.status_code < 600:
            from curl_cffi.requests.exceptions import HTTPError

            raise HTTPError(f"HTTP Error {self.status_code}", response=self)

    def json(self) -> Any:
        # Parse the raw bytes rather than ``self.text`` so a UTF-8 BOM or
        # another JSON-detectable encoding decodes like a live response.
        return json.loads(self.content)

    async def aclose(self) -> None:
        return None


def cache_enabled() -> bool:
    """Whether the scrape cache is active (``[http] cache``)."""
    return bool(http_setting("cache", True))


def cache_ttl_hours() -> int:
    """Freshness window for cached entries (``[http] cache-ttl``, default 6h).

    Clamped to ``_MAX_ENTRY_AGE_HOURS``: a TTL beyond the hard drop age would
    keep entries that the over-age check would otherwise clear.
    """
    ttl = http_setting("cache-ttl", _DEFAULT_TTL_HOURS)
    # ``bool`` is an ``int`` subclass; reject it so a 0/1 flag can't mint an
    # oddly tiny TTL.
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl <= 0:
        return _DEFAULT_TTL_HOURS
    return min(int(ttl), _MAX_ENTRY_AGE_HOURS)


def cache_max_bytes() -> int:
    """Total on-disk size budget that triggers the size sweep
    (``[http] cache-max-bytes``, default 50MB).

    Unlike the TTL/entry-cap, this is a hard ceiling: when the cache's total
    bytes exceed it, the oldest entries are evicted. An invalid value (bool,
    unparseable string, at or below zero) keeps the default so the sweep
    stays bounded.
    """
    value = http_setting("cache-max-bytes", _DEFAULT_MAX_BYTES)
    if isinstance(value, bool):
        return _DEFAULT_MAX_BYTES
    if isinstance(value, int) and value <= 0:
        return _DEFAULT_MAX_BYTES
    try:
        return parse_size_string(value)
    except ValueError:
        return _DEFAULT_MAX_BYTES


def _header_str(value: Any) -> str:
    """Normalize a header value for storage (curl_cffi emits lists for repeats)."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _cache_root() -> Path:
    if _CACHE_DIR_OVERRIDE is not None:
        return _CACHE_DIR_OVERRIDE
    return cache_dir() / "http"


def _cache_key(url: str, profile: str, extra_headers: dict[str, str]) -> str:
    h = hashlib.sha256()
    h.update(url.encode("utf-8"))
    h.update(b"\x00")
    h.update((profile or "").encode("utf-8"))
    h.update(b"\x00")
    normalized = sorted(
        (k.strip().lower(), _header_str(v).strip()) for k, v in extra_headers.items()
    )
    h.update(json.dumps(normalized).encode("utf-8"))
    return h.hexdigest()


def _entry_path(url: str, profile: str, extra_headers: dict[str, str]) -> Path:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise RequestBlockedError(f"cache entry URL must be http(s), got: {url!r}")
    return _cache_root() / f"{_cache_key(url, profile, extra_headers)}.dat"


def _unlink_best_effort(path: Path) -> None:
    """Remove ``path`` if present, ignoring failures (cache removal is a cleanup)."""
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def _maybe_sweep() -> None:
    """Expired/oversized-entry GC, throttled to at most once an hour.

    Scanning the directory is O(entries), so an idle or small cache never pays
    for it (reads and writes both trigger the sweep, throttled); an hour of
    activity is plenty of coverage without statting every file per request.

    Two bounds are enforced here rather than at the call site:

    - Age: entries whose file mtime is past the hard drop age are removed
      unconditionally. mtime tracks ``created`` because every write, refresh
      included, rewrites the file, so an old entry can never be kept alive by
      revalidation alone.
    - Size: once the cache's total bytes exceed :func:`cache_max_bytes`, the
      oldest entries are deleted until it is back at or under the budget.

    Orphaned temp files from interrupted writes are always cleared, and only
    past a short staleness window so a concurrent writer's in-flight file
    can't be mistaken for abandoned.
    """
    global _last_sweep
    now = time.time()
    if now - _last_sweep < _SWEEP_INTERVAL_SECONDS:
        return
    _last_sweep = now
    root = _cache_root()
    if not root.is_dir():
        return
    try:
        paths = [p for p in root.iterdir() if p.is_file()]
    except OSError:
        return
    dat_cutoff = now - _MAX_ENTRY_AGE_HOURS * 3600
    tmp_cutoff = now - _TMP_STALE_SECONDS
    by_mtime: list[tuple[float, int, Path]] = []
    total = 0
    for p in paths:
        try:
            st = p.stat()
        except OSError:
            continue
        if p.suffix == ".tmp":
            if st.st_mtime < tmp_cutoff:
                _unlink_best_effort(p)
            continue
        if p.suffix == ".dat":
            if st.st_mtime < dat_cutoff:
                _unlink_best_effort(p)
                continue
            by_mtime.append((st.st_mtime, st.st_size, p))
            total += st.st_size
    budget = cache_max_bytes()
    if total <= budget:
        return
    for _mtime, size, p in sorted(by_mtime):
        if total <= budget:
            break
        total -= size
        _unlink_best_effort(p)


def _read_entry(path: Path) -> dict[str, Any] | None:
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > _MAX_ENTRY_BYTES:
        _unlink_best_effort(path)
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 12 or not data.startswith(_MAGIC):
        _unlink_best_effort(path)
        return None
    try:
        (version,) = struct.unpack(">I", data[4:8])
        if version != _VERSION:
            _unlink_best_effort(path)
            return None
        (meta_len,) = struct.unpack(">I", data[8:12])
        if meta_len > len(data) - 12:
            _unlink_best_effort(path)
            return None
        meta = data[12 : 12 + meta_len].decode("utf-8")
        entry = json.loads(meta)
    except (ValueError, TypeError, struct.error, UnicodeDecodeError):
        _unlink_best_effort(path)
        return None
    # Malformed metadata can't be served, and keeping it around would shadow
    # every future fetch; remove it so the next run rebuilds fresh.
    if not isinstance(entry, dict):
        _unlink_best_effort(path)
        return None
    status = entry.get("status")
    if isinstance(status, bool) or not isinstance(status, int):
        _unlink_best_effort(path)
        return None
    entry["body"] = data[12 + meta_len :]
    return entry


def _write_entry(path: Path, entry: dict[str, Any]) -> None:
    body = entry["body"]
    meta = {k: v for k, v in entry.items() if k != "body"}
    blob = json.dumps(meta).encode("utf-8")
    payload = _MAGIC + struct.pack(">I", _VERSION) + struct.pack(">I", len(blob)) + blob + body
    tmp_path: str | None = None
    fd: int | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # A unique temp name per writer (rather than ``path.with_suffix``) so
        # concurrent writers for one key cannot truncate each other's temp
        # file; the rename to ``path`` is atomic, so readers only ever see a
        # full entry.
        fd, tmp_path = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        with os.fdopen(fd, "wb") as fh:
            fd = None
            fh.write(payload)
        os.replace(tmp_path, path)
        tmp_path = None
        _maybe_sweep()
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            if fd is not None:
                os.close(fd)
        with contextlib.suppress(OSError):
            if tmp_path is not None:
                os.unlink(tmp_path)


def _is_fresh(entry: dict[str, Any]) -> bool:
    created = entry.get("created")
    if isinstance(created, bool) or not isinstance(created, (int, float)):
        return False
    age = time.time() - created
    if age < 0:
        # A ``created`` ahead of the local clock (an NTP jump-back, or a
        # planted entry) would otherwise stay "fresh" until the clock caught
        # up. Treat it as instantly stale so the next run revalidates and, on
        # success, rewrites ``created`` to now.
        return False
    return age < cache_ttl_hours() * 3600


def _entry_age_hours(entry: dict[str, Any]) -> float:
    created = entry.get("created")
    if isinstance(created, bool) or not isinstance(created, (int, float)):
        # Unknown age must not survive the over-age check.
        return _MAX_ENTRY_AGE_HOURS + 1.0
    return (time.time() - created) / 3600


def conditional_headers(entry: dict[str, Any]) -> dict[str, str]:
    """``If-None-Match``/``If-Modified-Since`` validators for a stale entry."""
    out: dict[str, str] = {}
    etag = entry.get("etag")
    last_modified = entry.get("last_modified")
    if etag:
        out["If-None-Match"] = etag
    if last_modified:
        out["If-Modified-Since"] = last_modified
    return out


def lookup(
    url: str,
    profile: str,
    extra_headers: dict[str, str],
    method: str = "GET",
) -> tuple[CachedResponse | None, dict[str, Any] | None]:
    """Read the cache entry for ``url``.

    Returns ``(response, stale_entry)``. When the entry is fresh,
    ``response`` is a :class:`CachedResponse` and ``stale_entry`` is ``None``
    (serve without network). When the entry is stale, ``response`` is ``None``
    and ``stale_entry`` carries the body/validators for a conditional recheck.
    Over-age entries are dropped on read. No-op when the cache is disabled or
    for non-GET methods.
    """
    if not cache_enabled() or method.upper() != "GET":
        return None, None
    _maybe_sweep()
    path = _entry_path(url, profile, extra_headers)
    entry = _read_entry(path)
    if entry is None:
        return None, None
    if _entry_age_hours(entry) > _MAX_ENTRY_AGE_HOURS:
        _unlink_best_effort(path)
        return None, None
    if _is_fresh(entry):
        return CachedResponse(entry), None
    return None, entry


def store(
    url: str,
    profile: str,
    extra_headers: dict[str, str],
    *,
    method: str = "GET",
    status: int,
    headers: dict[str, str | list[str]],
    body: bytes,
    created: float | None = None,
) -> None:
    """Persist a 2xx GET response body for ``url``.

    Best-effort and silent on failure. No-op when the cache is disabled, the
    method is not GET, the body is missing, the status is not 2xx, or the
    response carries ``Set-Cookie``.
    """
    if not cache_enabled() or method.upper() != "GET":
        return
    if body is None or not 200 <= int(status) < 300:
        return
    # Finding Set-Cookie among the raw response headers is a documented
    # approximation (curl_cffi may combine repeats); the privacy guarantee is
    # "no session cookies land on disk", not an exhaustive header audit.
    if any(k.lower() == "set-cookie" for k in headers):
        return
    etag = next((v for k, v in headers.items() if k.lower() == "etag"), None)
    last_modified = next(
        (v for k, v in headers.items() if k.lower() == "last-modified"), None
    )
    entry = {
        "status": int(status),
        "headers": {k: _header_str(v) for k, v in headers.items()},
        "body": body,
        "created": (
            created if created is not None and not isinstance(created, bool) else time.time()
        ),
        "etag": _header_str(etag) if etag else None,
        "last_modified": _header_str(last_modified) if last_modified else None,
    }
    _write_entry(_entry_path(url, profile, extra_headers), entry)


def refresh(
    url: str,
    profile: str,
    extra_headers: dict[str, str],
    stale: dict[str, Any],
    method: str = "GET",
) -> None:
    """Re-extend a stale entry's TTL after a ``304`` (keep body + validators).

    No-op when the cache is disabled, the method is not GET, or ``stale`` is
    not a parseable entry (must carry ``status`` and ``body``).
    """
    if not cache_enabled() or method.upper() != "GET":
        return
    if not isinstance(stale, dict) or "body" not in stale or "status" not in stale:
        return
    entry = dict(stale)
    entry["created"] = time.time()
    _write_entry(_entry_path(url, profile, extra_headers), entry)


def clear() -> int:
    """Delete every cache file, including orphaned write temps; returns count."""
    root = _cache_root()
    if not root.is_dir():
        return 0
    removed = 0
    try:
        for path in root.iterdir():
            if path.is_file() and path.suffix in (".dat", ".tmp"):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
    except OSError:
        pass
    return removed


def cache_dir_path() -> Path:
    """The directory holding cache entries (for ``comic-dl cache path``)."""
    return _cache_root()
