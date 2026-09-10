"""Persistent SQLite cookie jar (RFC 6265 subset) shared across runs."""

from __future__ import annotations

import contextlib
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .config import config_dir

_CREATE_COOKIES = """
CREATE TABLE IF NOT EXISTS cookies (
    host       TEXT NOT NULL,
    path       TEXT NOT NULL DEFAULT '/',
    name       TEXT NOT NULL,
    value      TEXT NOT NULL,
    expires    INTEGER,
    secure     INTEGER NOT NULL DEFAULT 0,
    http_only  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (host, path, name)
)
"""

_DB_NAME = "cookies.db"

# A cookie stored for a *public suffix* is replayed to every subdomain of it,
# so a malicious site can plant a value every co-tenant then receives (a
# hostile *.github.io page can poison the jar for all *.github.io visits via
# ``Domain=.github.io``).  Single-label hosts (bare TLDs) are always public
# suffixes and are rejected outright.  For two-label hosts the precise test
# needs the Mozilla Public Suffix List, which is not in the stdlib and not a
# dependency here; this curated subset covers the suffixes most relevant to
# scraping contexts.
# ponytail: curated, not the full PSL — swap in a PSL-backed check
# (publicsuffix2) if co-tenant leakage on unlisted two-label suffixes matters.
_TWO_LABEL_PUBLIC_SUFFIXES = frozenset({
    "github.io",
    "co.uk", "org.uk", "ac.uk",
    "com.au", "net.au", "org.au",
    "com.br", "net.br", "org.br",
    "co.nz", "net.nz", "org.nz",
    "com.mx", "com.ar",
    "co.jp", "co.kr", "com.cn",
    "co.in", "net.in", "org.in",
    "co.id", "com.tw", "com.hk", "com.sg", "com.my", "com.ph",
})


def _is_public_suffix_host(host: str) -> bool:
    """True when ``host`` is a public-suffix label anyone can set cookies for."""
    host = host.lstrip(".").lower()
    labels = host.split(".")
    if len(labels) < 2:
        return host != "localhost"
    return host in _TWO_LABEL_PUBLIC_SUFFIXES


class CookieJar:
    """Persistent, per-domain cookie store backed by SQLite (WAL).

    Follows the RFC 6265 subset needed by scrapers: matching by domain
    suffix, honoring ``expires`` (``NULL`` = session cookie, kept for this
    process only) and ``Secure`` (never returned over plain HTTP).
    ``path`` and ``HttpOnly`` are stored but deliberately NOT enforced on
    read — every matching host cookie is returned for any request path, and
    a single-label host (``localhost``) also matches subdomains of it. This
    covers the scraping cases that matter and is a known, accepted deviation
    from RFC 6265. Failures are silent — a broken or unwritable store never
    breaks downloads.

    Writes are serialized with a lock; each operation opens its own short
    connection so concurrent async tasks (and ``asyncio.to_thread`` callers)
    can share one instance safely.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (config_dir() / _DB_NAME)
        self._lock = threading.Lock()
        self._session_only: dict[tuple[str, str, str], str] = {}

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, timeout=5)
        self._restrict_perms()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(_CREATE_COOKIES)
        except sqlite3.Error:
            conn.close()
            raise
        return conn

    def _restrict_perms(self) -> None:
        """Owner-only (0600) perms on the store and its WAL sidecars.

        The DB holds session tokens and ``cf_clearance`` values; browser
        cookie stores are user-readable only, and this one should be too.
        Applied on every connect (idempotent, covers files created by a
        previous run under a different umask).
        """
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self._path}{suffix}")
            with contextlib.suppress(OSError):
                candidate.chmod(0o600)

    # -- read ---------------------------------------------------------------

    def cookies_for(self, host: str, *, https: bool = False) -> dict[str, str]:
        """Non-expired ``{name: value}`` cookies matching ``host``.

        ``host`` matches a stored domain exactly, or as a subdomain
        (``api.kagane.to`` matches a stored ``kagane.to``). Cookies flagged
        ``Secure`` are only returned when ``https`` is set — they must never
        ride a plain-HTTP request to their own host. Session-only cookies
        (this process) are merged in.
        """
        host = (host or "").lower()
        out: dict[str, str] = {}
        if not host:
            return out
        now = int(time.time())
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT path, name, value, expires, secure "
                    "FROM cookies WHERE host = ?",
                    (host,),
                ).fetchall()
                rows += conn.execute(
                    "SELECT path, name, value, expires, secure "
                    "FROM cookies WHERE host != ? AND (? LIKE '%.' || host)",
                    (host, host),
                ).fetchall()
        except sqlite3.Error:
            return out
        for _path, name, value, expires, secure in rows:
            if expires is not None and expires <= now:
                continue
            if secure and not https:
                continue
            out[name] = value
        with self._lock:
            for (h, _p, name), value in self._session_only.items():
                if h == host or (host.endswith(f".{h}") if h else False):
                    out[name] = value
        return out

    def list(self, host: str | None = None) -> list[dict]:
        """Rows for inspection: host, path, name, expires (epoch or None).

        Expired rows are excluded. When ``host`` is given, only rows for
        that exact host are returned.
        """
        rows: list[dict] = []
        now = int(time.time())
        try:
            with self._connect() as conn:
                if host:
                    cur = conn.execute(
                        "SELECT host, path, name, expires FROM cookies WHERE host = ?",
                        (host.lower(),),
                    )
                else:
                    cur = conn.execute(
                        "SELECT host, path, name, expires FROM cookies"
                    )
                for host_, path, name, expires in cur.fetchall():
                    if expires is not None and expires <= now:
                        continue
                    rows.append(
                        {
                            "host": host_,
                            "path": path,
                            "name": name,
                            "expires": expires,
                        }
                    )
        except sqlite3.Error:
            pass
        with self._lock:
            for (h, p, name), _value in self._session_only.items():
                if host and h != host.lower():
                    continue
                rows.append({"host": h, "path": p, "name": name, "expires": None})
        rows.sort(key=lambda r: (r["host"], r["name"], r["path"]))
        return rows

    # -- write --------------------------------------------------------------

    def store_cookiejar(self, cookiejar: Any) -> None:
        """Persist cookies from a ``http.cookiejar.CookieJar`` (full attrs).

        curl_cffi keeps an RFC-compliant :class:`http.cookiejar.CookieJar`
        per session whose cookies carry domain/path/expires/secure — this is
        what we feed into the store after each response. Session cookies
        (no expiry) are kept in memory for this process only.
        """
        now = int(time.time())
        rows: list[tuple[str, str, str, str, int, int, int]] = []
        session: list[tuple[str, str, str, str]] = []
        for c in cookiejar:
            if c.name is None or c.value is None:
                continue
            host = (c.domain or "").lstrip(".").lower()
            if not host or _is_public_suffix_host(host):
                continue
            path = c.path or "/"
            if c.expires is None:
                session.append((host, path, c.name, c.value))
                continue
            if c.expires <= now:
                continue
            http_only = 1 if _has_nonstandard_attr(c, "HttpOnly") else 0
            rows.append(
                (host, path, c.name, c.value, int(c.expires), 1 if c.secure else 0, http_only)
            )
        if rows:
            try:
                with self._lock, self._connect() as conn:
                    conn.executemany(
                        """
                        INSERT INTO cookies (host, path, name, value, expires, secure, http_only)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(host, path, name) DO UPDATE SET
                            value = excluded.value,
                            expires = excluded.expires,
                            secure = excluded.secure,
                            http_only = excluded.http_only
                        """,
                        rows,
                    )
            except sqlite3.Error:
                pass
        if session:
            with self._lock:
                for host, path, name, value in session:
                    self._session_only[(host, path, name)] = value

    def set(
        self,
        host: str,
        name: str,
        value: str,
        *,
        path: str = "/",
        expires: int | None = None,
    ) -> None:
        """Explicitly store a cookie (used by challenge-solver harvests)."""
        if _is_public_suffix_host(host or ""):
            return
        if expires is not None and expires <= int(time.time()):
            self.delete(host, name, path)
            return
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO cookies (host, path, name, value, expires, secure, http_only)
                    VALUES (?, ?, ?, ?, ?, 0, 0)
                    ON CONFLICT(host, path, name) DO UPDATE SET
                        value = excluded.value,
                        expires = excluded.expires
                    """,
                    (host.lower(), path, name, value, expires),
                )
        except sqlite3.Error:
            pass

    def delete(self, host: str, name: str, path: str = "/") -> None:
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "DELETE FROM cookies WHERE host = ? AND name = ? AND path = ?",
                    (host.lower(), name, path),
                )
        except sqlite3.Error:
            pass

    def clear(self, host: str | None = None) -> None:
        """Drop all cookies, or just one host's."""
        try:
            with self._lock, self._connect() as conn:
                if host:
                    conn.execute("DELETE FROM cookies WHERE host = ?", (host.lower(),))
                else:
                    conn.execute("DELETE FROM cookies")
                self._session_only.clear()
        except sqlite3.Error:
            pass

    def flush(self) -> None:
        """Remove expired rows (best-effort)."""
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "DELETE FROM cookies WHERE expires IS NOT NULL AND expires <= ?",
                    (int(time.time()),),
                )
        except sqlite3.Error:
            pass

    def __len__(self) -> int:
        try:
            with self._connect() as conn:
                return int(conn.execute("SELECT COUNT(*) FROM cookies").fetchone()[0])
        except sqlite3.Error:
            return 0


def _has_nonstandard_attr(cookie: Any, name: str) -> bool:
    method = getattr(cookie, "has_nonstandard_attr", None)
    if callable(method):
        try:
            return bool(method(name))
        except Exception:
            return False
    return False
