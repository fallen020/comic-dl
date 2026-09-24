"""Persistent SQLite cookie jar (RFC 6265 subset) shared across runs."""

from __future__ import annotations

import contextlib
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from publicsuffix2 import PublicSuffixList  # type: ignore[import-untyped]  # third-party, no stubs

from .config import config_dir
from .cookiecrypt import decrypt_value, encrypt_value, is_encrypted, resolve_key
from .ui import print_warning

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

_warned_plaintext = False


def _warn_plaintext_once() -> None:
    """Emit the 'not encrypted' notice once per process.

    The warning is a hint, not an error: a missing keyring degrades the jar
    to plaintext so downloads still work. Shown once because every CookieJar
    construction that fails key resolution would otherwise spam the same line.
    """
    global _warned_plaintext
    if _warned_plaintext:
        return
    _warned_plaintext = True
    print_warning(
        "cookie jar not encrypted: no usable key found; set COMIC_DL_COOKIE_KEY "
        "or configure an OS keyring to encrypt stored cookies"
    )


# A cookie stored for a *public suffix* is replayed to every subdomain of it,
# so a malicious site can plant a value every co-tenant then receives (a
# hostile *.github.io page cannot poison the jar for all *.github.io visits,
# because ``Domain=.github.io`` is refused). The decision is backed by the
# Mozilla Public Suffix List (publicsuffix2), not a hand-sized subset: any
# host the PSL declares a public suffix is rejected. Single labels that are
# not declared suffixes (``intranet``), and ``localhost``, stay storable.
_psl_cache: PublicSuffixList | None = None


def _psl() -> PublicSuffixList:
    """Lazily-built PSL parser (parsing the embedded list costs a few ms)."""
    global _psl_cache
    if _psl_cache is None:
        # publicsuffix2 reads its bundled list with the deprecated
        # ``codecs.open``; the warning is theirs, fires once, and is not
        # actionable here.
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            _psl_cache = PublicSuffixList()
    return _psl_cache


def _is_public_suffix_host(host: str) -> bool:
    """True when ``host`` is a public-suffix label anyone can set cookies for."""
    host = host.lstrip(".").lower()
    if "." not in host:
        # A bare label is a TLD (reject) or a scoped intranet host (allow).
        return host != "localhost" and host in _psl().tlds
    try:
        return _psl().get_tld(host) == host
    except Exception:
        # Fail closed: an unparseable multi-label host is not a registrable
        # domain, so it must not be usable as a cookie namespace.
        return True


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

    All access is serialized with a lock around one lazily-opened persistent
    SQLite connection, so concurrent async tasks (and ``asyncio.to_thread``
    callers) can share one instance safely without paying per-request
    ``sqlite3.connect`` churn.
    """

    def __init__(self, path: Path | None = None, *, encryption: str = "auto") -> None:
        self._path = path or (config_dir() / _DB_NAME)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._session_only: dict[tuple[str, str, str], str] = {}
        self._key = resolve_key(encryption)
        if encryption != "off" and self._key is None:
            _warn_plaintext_once()

    def _encrypt(self, value: str) -> str:
        """Persist in the envelope only when a key exists, so a plaintext jar
        (``encryption="off"``) stays readable without one."""
        if self._key is None:
            return value
        return encrypt_value(self._key, value)

    def _decrypt(self, value: str) -> str | None:
        """Plaintext for a stored ``value``, or ``None`` when unreadable.

        Non-``enc1.`` values are legacy plaintext and pass through. An
        ``enc1.`` value with no key, a corrupt blob, or an unknown key
        authenticates nothing and is dropped from responses — serving it as
        raw plaintext would leak half the envelope as a cookie value.
        """
        if not is_encrypted(value):
            return value
        if self._key is None:
            return None
        return decrypt_value(self._key, value)

    def _connect(self) -> sqlite3.Connection:
        """Persistent per-instance connection (caller must hold ``_lock``).

        One connection is opened lazily and reused: the previous design opened
        a fresh ``sqlite3.connect`` plus the WAL/table setup per operation,
        which every outbound request paid for. WAL allows concurrent access;
        holding ``_lock`` serializes use of this single connection.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._conn is None:
            self._conn = sqlite3.connect(self._path, timeout=5, check_same_thread=False)
            self._restrict_perms()
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA busy_timeout=5000")
                self._conn.execute("PRAGMA synchronous=NORMAL")
                self._conn.execute(_CREATE_COOKIES)
            except sqlite3.Error:
                with contextlib.suppress(sqlite3.Error):
                    self._conn.close()
                self._conn = None
                raise
        return self._conn

    def _reset_conn(self) -> None:
        """Drop a broken persistent connection; the next operation rebuilds it.

        A short-lived connection previously recovered on its own from a
        transient error (next op made a fresh one); a shared connection would
        otherwise stay poisoned for the rest of the process.
        """
        if self._conn is not None:
            with contextlib.suppress(sqlite3.Error):
                self._conn.close()
            self._conn = None

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
        candidates: list[tuple[str, str, str, str, int, int]] = []
        try:
            with self._lock:
                conn = self._connect()
                candidates = conn.execute(
                    "SELECT host, path, name, value, expires, secure FROM cookies WHERE host = ?",
                    (host,),
                ).fetchall()
                candidates += conn.execute(
                    "SELECT host, path, name, value, expires, secure "
                    "FROM cookies WHERE host != ? AND (? LIKE '%.' || host)",
                    (host, host),
                ).fetchall()
        except sqlite3.Error:
            self._reset_conn()
            return out
        # Most-specific domain wins on name collisions (RFC 6265 §5.4): longer
        # hosts first, longer paths as the tie-break, and first-wins per name.
        # Without this ordering a suffix cookie would overwrite the more
        # specific exact-host value, since both can share one name.
        for _host, _path, name, value, expires, secure in sorted(
            candidates, key=lambda r: (-len(r[0]), -len(r[1]), r[2])
        ):
            if expires is not None and expires <= now:
                continue
            if secure and not https:
                continue
            plain = self._decrypt(value)
            if plain is None:
                continue
            out.setdefault(name, plain)
        # Session-only (this-process) cookies are fresher than anything
        # persisted, so they override the tier above; within that tier the same
        # specificity rule applies.
        with self._lock:
            session_winner: dict[str, str] = {}
            for (h, _p, name), value in sorted(
                self._session_only.items(),
                key=lambda kv: (-len(kv[0][0]), -len(kv[0][1]), kv[0][2]),
            ):
                if h == host or (host.endswith(f".{h}") if h else False):
                    session_winner.setdefault(name, value)
            out.update(session_winner)
        return out

    def list(self, host: str | None = None) -> list[dict]:
        """Rows for inspection: host, path, name, expires (epoch or None).

        Expired rows are excluded. When ``host`` is given, only rows for
        that exact host are returned.
        """
        rows: list[dict] = []
        now = int(time.time())
        try:
            with self._lock:
                conn = self._connect()
                if host:
                    cur = conn.execute(
                        "SELECT host, path, name, expires FROM cookies WHERE host = ?",
                        (host.lower(),),
                    )
                else:
                    cur = conn.execute("SELECT host, path, name, expires FROM cookies")
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
                for (h, p, name), _value in self._session_only.items():
                    if host and h != host.lower():
                        continue
                    rows.append({"host": h, "path": p, "name": name, "expires": None})
        except sqlite3.Error:
            self._reset_conn()
            pass
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
        to_delete: set[tuple[str, str, str]] = set()
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
                # A past expiry is the server's way of deleting a cookie
                # (``Expires``/``Max-Age`` in the past). Silently skipping
                # would leave the prior value stored, so the deleted token
                # keeps being re-sent; remove it instead.
                to_delete.add((host, path, c.name))
                continue
            http_only = 1 if _has_nonstandard_attr(c, "HttpOnly") else 0
            rows.append(
                (
                    host,
                    path,
                    c.name,
                    self._encrypt(c.value),
                    int(c.expires),
                    1 if c.secure else 0,
                    http_only,
                )
            )
        if rows or to_delete:
            try:
                with self._lock:
                    conn = self._connect()
                    with conn:
                        if to_delete:
                            conn.executemany(
                                "DELETE FROM cookies WHERE host = ? AND path = ? AND name = ?",
                                [(h, p, n) for h, p, n in sorted(to_delete)],
                            )
                        if rows:
                            conn.executemany(
                                (
                                    "INSERT INTO cookies "
                                    "(host, path, name, value, expires, secure, http_only) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                                    "ON CONFLICT(host, path, name) DO UPDATE SET "
                                    "value = excluded.value, "
                                    "expires = excluded.expires, "
                                    "secure = excluded.secure, "
                                    "http_only = excluded.http_only"
                                ),
                                rows,
                            )
                        # Sweep other expired rows while the write transaction is
                        # already open (best-effort hygiene; the read path filters
                        # them anyway, this just keeps the store honest and small).
                        conn.execute(
                            "DELETE FROM cookies WHERE expires IS NOT NULL AND expires <= ?",
                            (now,),
                        )
            except sqlite3.Error:
                self._reset_conn()
                pass
        if session or to_delete:
            with self._lock:
                for host, path, name, value in session:
                    self._session_only[(host, path, name)] = value
                for h, p, n in to_delete:
                    self._session_only.pop((h, p, n), None)

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
            with self._lock:
                conn = self._connect()
                with conn:
                    conn.execute(
                        """
                        INSERT INTO cookies (host, path, name, value, expires, secure, http_only)
                        VALUES (?, ?, ?, ?, ?, 0, 0)
                        ON CONFLICT(host, path, name) DO UPDATE SET
                            value = excluded.value,
                            expires = excluded.expires
                        """,
                        (host.lower(), path, name, self._encrypt(value), expires),
                    )
        except sqlite3.Error:
            self._reset_conn()
            pass

    def delete(self, host: str, name: str, path: str = "/") -> None:
        try:
            with self._lock:
                conn = self._connect()
                with conn:
                    conn.execute(
                        "DELETE FROM cookies WHERE host = ? AND name = ? AND path = ?",
                        (host.lower(), name, path),
                    )
        except sqlite3.Error:
            self._reset_conn()
            pass

    def clear(self, host: str | None = None) -> None:
        """Drop all cookies, or just one host's."""
        try:
            with self._lock:
                conn = self._connect()
                with conn:
                    if host:
                        conn.execute("DELETE FROM cookies WHERE host = ?", (host.lower(),))
                    else:
                        conn.execute("DELETE FROM cookies")
                self._session_only.clear()
        except sqlite3.Error:
            self._reset_conn()
            pass

    def flush(self) -> None:
        """Remove expired rows (best-effort)."""
        try:
            with self._lock:
                conn = self._connect()
                with conn:
                    conn.execute(
                        "DELETE FROM cookies WHERE expires IS NOT NULL AND expires <= ?",
                        (int(time.time()),),
                    )
        except sqlite3.Error:
            self._reset_conn()
            pass

    def __len__(self) -> int:
        try:
            with self._lock:
                conn = self._connect()
                return int(conn.execute("SELECT COUNT(*) FROM cookies").fetchone()[0])
        except sqlite3.Error:
            self._reset_conn()
            return 0

    def close(self) -> None:
        """Release the persistent connection (idempotent)."""
        with self._lock:
            self._reset_conn()

    def __enter__(self) -> CookieJar:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _has_nonstandard_attr(cookie: Any, name: str) -> bool:
    method = getattr(cookie, "has_nonstandard_attr", None)
    if callable(method):
        try:
            return bool(method(name))
        except Exception:
            return False
    return False
