"""URL validation, SSRF guarding, filename sanitization, and image magic-byte checks."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
import tarfile
import threading
import time
import unicodedata
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlsplit

import defusedxml.ElementTree as DET
from curl_cffi.const import CurlOpt

from .config import http_setting

# RFC 6598 Carrier-Grade NAT shared-address space (100.64.0.0/10). Not
# flagged by IPv4Address.is_private on all runtimes; never a public target.
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")

INVALID_FS_CHARS = re.compile(r'[<>:"/\\|?*]')
CONTROL_CHARS = re.compile(r'[\x00-\x1f\x7f-\x9f]')
MULTI_DASH = re.compile(r' - |--+')
MULTI_SPACE = re.compile(r'\s+')
DOS_RESERVED = {
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
}

_MAX_COMICINFO_XML = 1_048_576

PAWCHIVE_PATTERN = re.compile(
    r'^https?://(?:www\.)?pawchive\.pw/'
    r'(?:[^/]+)/user/(\d+)/post/(\d+)'
    r'/?'
)

E_HENTAI_PATTERN = re.compile(
    r'^https?://(?:www\.)?e-hentai\.org/g/(\d+)/([a-f0-9]+)'
    r'/?'
)

WEBTOON_PATTERN = re.compile(
    r"^https?://(?:www\.|m\.)?webtoons\.com"
    r"/([a-z]{2})/([^/]+)/([^/]+)"
    r"(?:/([^/]+))?"
    r"/(list|viewer)"
    r"\?title_no=(\d+)"
    r"(?:&episode_no=(\d+))?"
    r"/?$"
)

PART_PATTERN = re.compile(
    r'(?:part|chapter)\s*#?\s*(\d+)', re.IGNORECASE
)

GENERIC_CATEGORIES = frozenset({
    'fanfiction', 'manga', 'anime', 'doujinshi', 'artist cg', 'image set',
    'cosplay', 'game cg', 'western', 'non-h', 'misc', 'asian porn',
    'original',
})


LOW_SPEED_LIMIT_BPS = 1
LOW_SPEED_WINDOW_SECONDS = 20

HTTP_CLIENT_ARGS: dict[str, Any] = {
    "impersonate": "chrome146",
    "allow_redirects": True,
    "timeout": (15.0, 30.0),
    "max_clients": 10,
    "headers": {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        ),
        "Sec-Ch-Ua": (
            '"Chromium";v="146", "Not-A.Brand";v="24", '
            '"Google Chrome";v="146"'
        ),
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
    },
}

_SEARCH_ORIGINS = (
    "https://www.google.com/",
    "https://www.brave.com/search",
    "https://duckduckgo.com/",
    "https://www.bing.com/",
    "https://search.yahoo.com/",
)

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGT]?B)?\s*$", re.IGNORECASE)
_SIZE_UNITS = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}


def parse_size_string(raw: Any) -> int:
    """Parse a size config/flag value (``"50MB"``, ``512KB``, ``2GB``, plain bytes).

    Returns bytes. Invalid values raise ``ValueError``; callers decide between
    error promotion (CLI) and fallback (config default).
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise ValueError(f"invalid size: {raw!r}")
    if isinstance(raw, int):
        if raw < 0:
            raise ValueError(f"invalid size: {raw!r} (must be non-negative)")
        return raw
    raw = raw.strip()
    try:
        value = int(raw)
    except ValueError:
        pass
    else:
        if value < 0:
            raise ValueError(f"invalid size: {raw!r} (must be non-negative)")
        return value
    m = _SIZE_RE.match(raw)
    if not m:
        raise ValueError(f"invalid size: {raw!r}")
    num = float(m.group(1))
    unit = (m.group(2) or "B").upper()
    return int(num * _SIZE_UNITS[unit])


def _search_referer_for_host(host: str) -> str:
    """Deterministic search-engine referrer for a given host.

    Uses a simple hash of the host to pick from _SEARCH_ORIGINS so the same
    host always gets the same referrer within a run (stable identity), while
    different hosts are distributed across the pool.
    """
    if not host:
        return _SEARCH_ORIGINS[0]
    h = 0x811c9dc5
    for ch in host.encode("utf-8"):
        h ^= ch
        h = (h * 0x01000193) & 0xFFFFFFFF
    return _SEARCH_ORIGINS[h % len(_SEARCH_ORIGINS)]


def search_referer(host: str | None = None) -> str:
    """Return a search-engine referrer for ``host`` (or a default if None).

    This is the public helper used by callers that need a humane referrer.
    The choice is stable per host so a given source consistently presents the
    same entry-point referrer.
    """
    return _search_referer_for_host(host or "")


_DEPRECATED_IMPERSONATE = frozenset({
    "chrome99", "chrome100", "chrome101", "chrome104", "edge99", "edge101",
    "safari15_3", "safari15_5",
})


def known_impersonate_profiles() -> frozenset[str]:
    """Return the impersonation profile names this curl_cffi build accepts.

    The authoritative list comes from curl_cffi's ``BrowserType`` enum when
    it is importable; a static fallback keeps the check useful even if the
    binding changes shape. Values are lowercased to match how the profile is
    applied (``http_setting``/``http_client_args`` pass the string through).
    """
    try:
        from curl_cffi.requests import BrowserType

        names = {
            name for name in dir(BrowserType) if not name.startswith("_")
        }
    except Exception:
        names = {
            "chrome99", "chrome100", "chrome101", "chrome104", "chrome107",
            "chrome110", "chrome116", "chrome119", "chrome120", "chrome123",
            "chrome124", "chrome131", "chrome133a", "chrome136", "chrome142",
            "chrome145", "chrome146", "edge99", "edge101", "firefox133",
            "firefox135", "firefox144", "firefox147", "safari153", "safari155",
            "safari15_3", "safari15_5", "safari170", "safari17_0", "safari172_ios",
            "safari17_2_ios", "safari180", "safari180_ios", "safari18_0",
            "safari18_0_ios", "safari184", "safari184_ios", "safari260",
            "safari2601", "safari260_ios", "tor145",
        }
    return frozenset(name.lower() for name in names)


def validate_impersonate(profile: str) -> str | None:
    """Return an error message for an unsupported ``profile``, else ``None``.

    Only checks the profile against the known set; the user-facing warning for
    old-but-valid profiles is emitted separately at the CLI layer so config
    and flag sources are handled in one place.
    """
    known = known_impersonate_profiles()
    if profile.lower() not in known:
        return (
            f"unknown impersonation profile {profile!r}; "
            f"known profiles: {', '.join(sorted(known))}"
        )
    return None


def impersonate_is_deprecated(profile: str) -> bool:
    """True when ``profile`` is valid but tracks a very old browser build."""
    return profile.lower() in _DEPRECATED_IMPERSONATE


ALLOWED_SCHEMES = frozenset({"http", "https"})
MAX_REDIRECTS = 5


class RequestBlockedError(Exception):
    """An outbound request was refused on safety grounds (non-http(s),
    or a host resolving to a loopback/private/link-local/metadata address)."""


def _is_ip_unsafe(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # RFC 6598 shared-address space (100.64.0.0/10) is not flagged by
    # ip.is_private on all runtimes, yet it is not a public routable target.
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_NET:
        return True
    return bool(
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_unspecified
        or ip.is_reserved
        or ip.is_multicast
    )


def _resolve_host_unsafe(host: str) -> bool:
    """Blocking resolution+vetting of ``host``. Callers should prefer the
    cached wrappers (:func:`_host_unsafe` / :func:`_host_unsafe_async`)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if _is_ip_unsafe(addr):
            return True
    return False


_DNS_CACHE_TTL = 60.0
_dns_cache: dict[str, tuple[float, bool]] = {}
_dns_lock = threading.Lock()


def _cached_host_verdict(host: str) -> tuple[bool, bool]:
    """Return ``(unsafe, cache_hit)`` for a hostname verdict."""
    now = time.monotonic()
    with _dns_lock:
        entry = _dns_cache.get(host)
        if entry is not None and entry[0] > now:
            return entry[1], True
    unsafe = _resolve_host_unsafe(host)
    with _dns_lock:
        _dns_cache[host] = (now + _DNS_CACHE_TTL, unsafe)
    return unsafe, False


def clear_dns_cache() -> None:
    """Drop cached host verdicts (config change / tests)."""
    with _dns_lock:
        _dns_cache.clear()


def _host_unsafe(host: str) -> bool:
    """True when ``host`` is, or resolves to, a local/private network address."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return _is_ip_unsafe(addr)
    unsafe, _hit = _cached_host_verdict(host)
    return unsafe


async def _host_unsafe_async(host: str) -> bool:
    """Non-loop-blocking variant of :func:`_host_unsafe`.

    Literal IPs and cached verdicts resolve inline; only an actual DNS
    lookup runs in a worker thread, so a stalled system resolver cannot
    freeze every concurrent task on the event loop.
    """
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return _is_ip_unsafe(addr)
    now = time.monotonic()
    with _dns_lock:
        entry = _dns_cache.get(host)
        if entry is not None and entry[0] > now:
            return entry[1]
    unsafe = await asyncio.to_thread(_resolve_host_unsafe, host)
    with _dns_lock:
        _dns_cache[host] = (time.monotonic() + _DNS_CACHE_TTL, unsafe)
    return unsafe


def validate_request_url(url: str) -> str:
    """Validate an outbound URL. Returns it unchanged, or raises
    :class:`RequestBlockedError` when it must not be fetched."""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise RequestBlockedError(f"blocked non-http(s) URL scheme: {scheme or '<none>'!r}")
    host = parsed.hostname
    if not host:
        raise RequestBlockedError("URL has no host")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if _host_unsafe(host):
        raise RequestBlockedError(f"blocked request to local/private address: {host!r}")
    return url


async def validate_request_url_async(url: str) -> str:
    """Async twin of :func:`validate_request_url` that never blocks the loop."""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise RequestBlockedError(f"blocked non-http(s) URL scheme: {scheme or '<none>'!r}")
    host = parsed.hostname
    if not host:
        raise RequestBlockedError("URL has no host")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if await _host_unsafe_async(host):
        raise RequestBlockedError(f"blocked request to local/private address: {host!r}")
    return url


def resolve_redirect_url(base: str, location: str) -> str:
    """Resolve a ``Location`` header against the referring URL and validate it.

    Raises :class:`RequestBlockedError` if the resolved hop targets a disallowed
    scheme or local/private host. Callers must use this for *every* redirect hop
    so a public URL can never redirect onto an internal endpoint.
    """
    joined = urljoin(base, location)
    return validate_request_url(joined)


async def resolve_redirect_url_async(base: str, location: str) -> str:
    """Async twin of :func:`resolve_redirect_url`."""
    joined = urljoin(base, location)
    return await validate_request_url_async(joined)


def _with_referer(referer_url: str) -> dict[str, Any]:
    return http_client_args(referer_url=referer_url)


def referer_headers(referer_url: str) -> dict[str, str]:
    """Per-request ``Referer``/``Origin`` headers derived from ``referer_url``.

    Image CDNs that enforce hotlinking (e.g. kagane.to's ``kstatic.to``) check
    both, so callers that build requests outside a session's default headers
    (a shared cover session, for instance) can pass these per-request.
    """
    parsed = urlsplit(referer_url)
    origin = (
        f"{parsed.scheme}://{parsed.netloc}"
        if parsed.scheme and parsed.netloc
        else ""
    )
    headers: dict[str, str] = {"Referer": referer_url}
    if origin:
        headers["Origin"] = origin
    return headers


def http_client_args(
    *, referer_url: str | None = None, host: str | None = None
) -> dict[str, Any]:
    """HTTP client kwargs with the effective impersonate profile applied.

    ``[http] impersonate`` (or ``--impersonate``) overrides the built-in
    ``chrome146`` profile; anything else keeps the default. ``referer_url``
    merges a ``Referer`` header (and a matching ``Origin`` derived from its
    scheme+host) when the caller needs one — image CDNs that enforce
    hotlinking, such as kagane.to's ``kstatic.to``, check both.

    If ``referer_url`` is not provided but ``host`` is, a deterministic
    search-engine referrer is added for humane requests (rotating across
    Google, Brave, DuckDuckGo, Bing, Yahoo). Explicit hotlink referers always
    win when ``referer_url`` is given.
    """
    args: dict[str, Any] = dict(HTTP_CLIENT_ARGS)
    profile = http_setting("impersonate", "chrome146")
    if isinstance(profile, str) and profile.strip():
        args["impersonate"] = profile.strip()
    args["curl_options"] = {
        CurlOpt.LOW_SPEED_LIMIT: LOW_SPEED_LIMIT_BPS,
        CurlOpt.LOW_SPEED_TIME: LOW_SPEED_WINDOW_SECONDS,
    }

    effective_referer = referer_url
    if effective_referer is None and host:
        effective_referer = _search_referer_for_host(host)

    if effective_referer:
        headers: dict[str, str] = {**args["headers"], **referer_headers(effective_referer)}
        args = {**args, "headers": headers}
    return args


def clean_title(s: str) -> str:
    """Strip leading labels and trailing annotations from a raw title."""
    s = s.strip().rstrip(",").strip()
    bracket_start = s.find('[')
    bracket_end = s.find(']')
    if bracket_start == 0 and bracket_end > bracket_start:
        s = s[bracket_end + 1:].strip()
    dash = s.find(' - ')
    if dash > 0:
        s = s[dash + 3:].strip()
    if s.count('(') > s.count(')'):
        s = s[:s.rfind('(')].strip()
    if s.count('[') > s.count(']'):
        s = s[:s.rfind('[')].strip()
    s = re.sub(r'\s*\([^)]*\)\s*$', '', s)
    s = re.sub(r'\s*\[[^\]]*\]\s*$', '', s)
    return s.strip()


IMAGE_MAGIC: list[tuple[bytes, int, str]] = [
    (b'\xff\xd8\xff', 0, 'jpeg'),
    (b'\x89PNG\r\n\x1a\n', 0, 'png'),
    (b'GIF87a', 0, 'gif'),
    (b'GIF89a', 0, 'gif'),
    (b'RIFF', 0, 'webp'),
    (b'BM', 0, 'bmp'),
    (b'\x00\x00\x01\x00', 0, 'ico'),
    (b'ftypavif', 4, 'avif'),
]

def image_source_name(page_number: int, url: str) -> str:
    """Return the stable on-disk filename for a page image."""
    path = url.split("?")[0].split("#")[0]
    try:
        _, ext = path.rsplit(".", 1)
    except ValueError:
        ext = "jpg"
    ext = ext.lower()
    if ext not in ("jpg", "jpeg", "png", "webp", "gif", "bmp", "avif"):
        ext = "jpg"
    return f"page_{page_number:04d}.{ext}"


def sanitize_filename(name: str, max_len: int = 200) -> str:
    """Make ``name`` safe as a file/directory component on any OS.

    Why the extra strictness over POSIX's own rules: archives and folders
    produced on Linux routinely get copied to Windows (NTFS, case-insensitive,
    reserved device names) and macOS (APFS, case-insensitive, NFD-normalizing),
    so a name must survive the most restrictive common denominator. That means
    NFC-canonical Unicode, no control characters, and no reserved ``CON``-style
    base names — the net effect is deterministic, collation-stable names no
    matter which platform originally wrote them.
    """
    name = unicodedata.normalize("NFC", name)
    name = CONTROL_CHARS.sub("", name)
    name = INVALID_FS_CHARS.sub("-", name)
    name = MULTI_DASH.sub("-", name)
    name = MULTI_SPACE.sub(" ", name)
    name = name.strip(" .-")
    stem = name.rstrip(".").rstrip() or "untitled"
    # Windows reserves more than the bare token: "CON.txt", "AUX.1" —
    # anything whose base (up to the first dot) is a device name.
    if stem.partition(".")[0].rstrip().lower() in DOS_RESERVED:
        stem = f"_{stem}"
    if len(stem) > max_len:
        stem = stem[:max_len].rstrip()
    return stem


def ensure_unique_dir(parent: Path, title: str, max_len: int = 200) -> Path:
    """Create and return a series directory under ``parent`` without
    case-variant collisions.

    .. WHY:: APFS and NTFS fold case by default, so two otherwise-distinct
       series ("One Piece" vs "one piece") would silently share one directory
       there while staying separate on Linux — a download could merge or
       clobber a different title. An earlier exact-match directory is reused
       (re-downloads must land in the same folder), but any other sibling that
       differs only in case gets a deterministic ``" (2)"`` suffix so archives
       stay copy-safe across platforms.
    """
    base = sanitize_filename(title, max_len=max_len)
    parent.mkdir(parents=True, exist_ok=True)
    seen: dict[str, bool] = {}
    exact: set[str] = set()
    with os.scandir(parent) as it:
        for entry in it:
            exact.add(entry.name)
            seen.setdefault(entry.name.casefold(), entry.is_dir())
    name = base
    n = 2
    while True:
        key = name.casefold()
        if key not in seen:
            path = parent / name
            path.mkdir(exist_ok=True)
            return path
        # Reuse only an exact-name directory: on case-folding filesystems
        # .exists() also matches case variants, which would return a
        # colliding sibling instead of suffixing per the contract above.
        if name in exact and seen[key]:
            return parent / name
        name = f"{base} ({n})"
        n += 1


def normalize_url(url: str) -> str:
    """Normalize a URL to https, lowercase host, and no default port."""
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.hostname:
        return url
    scheme = "https"
    hostname = parsed.hostname.lower()
    try:
        port = parsed.port
    except ValueError:
        return url
    if port == 443:
        port = None
    netloc = hostname if port is None else f"{hostname}:{port}"
    path = parsed.path.rstrip("/") or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    return f"{scheme}://{netloc}{path}{query}{fragment}"


def normalize_url_key(url: str) -> str:
    """Normalized URL identity for dedup/skip checks, fragment stripped.

    A URL fragment marks a position within a page, so two chapters that
    differ only in fragment (``page#a`` vs ``page#b``) are the same resource
    and must not be deduplicated as distinct. ``normalize_url`` keeps the
    fragment for fetching; use this when comparing identity only.
    """
    normalized = normalize_url(url)
    if "#" in normalized:
        normalized, _, _ = normalized.partition("#")
    return normalized


def cbz_source_url(path: Path) -> str:
    """Return the source URL embedded in a comic archive's ComicInfo.xml, or ''.

    Reads ``.cbz``/``.zip`` (zip) and ``.cbt`` (tar) containers alike; the
    format is detected from the suffix.
    """
    try:
        if path.suffix.lower() == ".cbt":
            with tarfile.open(path, "r") as tf:
                member = tf.getmember("ComicInfo.xml")
                if member.size > _MAX_COMICINFO_XML:
                    return ""
                f = tf.extractfile(member)
                if f is None:
                    return ""
                data = f.read()
        else:
            with zipfile.ZipFile(path) as zf:
                info = zf.getinfo("ComicInfo.xml")
                if info.file_size > _MAX_COMICINFO_XML:
                    return ""
                data = zf.read("ComicInfo.xml")
    except (OSError, zipfile.BadZipFile, tarfile.TarError, KeyError):
        return ""
    try:
        root = DET.fromstring(data)
    except Exception:
        return ""
    return (root.findtext("Web") or "").strip()


def is_valid_pawchive_url(url: str) -> bool:
    """True when ``url`` looks like a pawchive post link."""
    if not url.startswith(("http://", "https://")):
        return False
    return bool(PAWCHIVE_PATTERN.match(url))


def is_valid_ehentai_url(url: str) -> bool:
    """True when ``url`` looks like an e-hentai gallery link."""
    if not url.startswith(("http://", "https://")):
        return False
    return bool(E_HENTAI_PATTERN.match(url))


def is_valid_webtoon_url(url: str) -> bool:
    """True when ``url`` looks like a WEBTOON episode or series link."""
    if not url.startswith(("http://", "https://")):
        return False
    return bool(WEBTOON_PATTERN.match(url))


def verify_image_bytes(data: bytes) -> str | None:
    """Detect an image format from a byte buffer via magic bytes, or None."""
    for magic, offset, fmt in IMAGE_MAGIC:
        if len(data) < offset + len(magic):
            continue
        if fmt == 'webp':
            if len(data) < 12:
                continue
            if data[0:4] == b'RIFF' and data[8:12] == b'WEBP':
                return 'webp'
        elif data[offset:offset + len(magic)] == magic:
            return fmt
    return None


MAGIC_MAX = max(
    max(offset + len(magic) for magic, offset, _ in IMAGE_MAGIC),
    12,
)


def verify_image_file(path: Path) -> str | None:
    """Detect an image format from a file header via magic bytes, or None."""
    try:
        with open(path, 'rb') as f:
            header = f.read(MAGIC_MAX)
    except (OSError, PermissionError):
        return None
    if not header:
        return None
    return verify_image_bytes(header)
