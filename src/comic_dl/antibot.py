"""Vendor-aware anti-bot challenge classifier.

Turns any HTTP response into a structured verdict identifying the vendor,
challenge type, block reason, and honeypot suspicion. Pure Python, no
external deps — suitable for offline testing with fixture responses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .http import get_jar
from .ui import trace


@dataclass(frozen=True)
class BlockVerdict:
    """Structured classification of a blocked/challenged response."""

    vendor: str
    # "cloudflare" | "recaptcha" | "hcaptcha" | "datadome" | "akamai" |
    # "perimeterx" | "kasada" | "imperva" | "unknown"
    kind: str
    # "interstitial" | "puzzle" | "score" | "waf" | "geo" | "rate" | "honeypot" | "none"
    reason: str | None
    # human-readable block reason (incl. CF error #)
    challenge: str | None
    # "non-interactive" | "managed" | "interactive" | "turnstile" |
    # "under-attack" | "__cf_bm" | None
    honeypot: bool
    # suspected AI-Labyrinth / fake 200
    recommended_action: str | None = None
    # Suggested action: "retry" | "solve" | "webview" | "impersonation" | "rate-limit" | None


# Cloudflare markers
_CF_SERVER = frozenset({"cloudflare", "cloudflare-nginx"})
_CF_HEADERS = frozenset({
    "cf-ray",
    "cf-mitigated",
    "cf-chl-bypass",
    "cf-request-id",
    "server",
})
_CF_BODY_MARKERS = (
    "challenge-error-title",
    "challenge-error-text",
    "cf-turnstile",
    "challenges.cloudflare.com",
    "captcha-container",
    "turnstile-widget",
    "ray-id",
    "data-ray",
    "__cf_bm",
    "cf_chl_rc_ni",
    "cf_chl_opt",
    "cType",
)

# CAPTCHA markers
_RECAPTCHA_V2_MARKERS = (
    "g-recaptcha",
    "recaptcha/api.js",
    "___grecaptcha_cfg",
    "grecaptcha.render",
)
_RECAPTCHA_V3_MARKERS = (
    "recaptcha/api.js?render=",
    "grecaptcha.execute",
    "grecaptcha.ready",
)
_HCAPTCHA_MARKERS = (
    "hcaptcha.com/1/api.js",
    "h-captcha",
    "data-hcaptcha-sitekey",
    "hcaptcha.render",
)
_TURNSTILE_MARKERS = (
    "cf-turnstile",
    "turnstile-widget",
    "challenges.cloudflare.com/turnstile",
    "turnstile.render",
)

# WAF vendor markers
_DATADOME_MARKERS = frozenset({"datadome", "dd-", "x-datadome"})
_AKAMAI_MARKERS = frozenset({"_abck", "sensor_data", "akamai", "akamaihd", "bm_sz", "bm_sv"})
_PERIMETERX_MARKERS = frozenset({
    "_px3", "_pxhd", "_pxvid", "/api/v2/collector",
    "perimeterx", "px-captcha",
})
_KASADA_MARKERS = frozenset({"x-kpsdk-", "kasada", "kpsdk", "cd_", "ct_"})
_IMPERVA_MARKERS = frozenset({"incap_ses", "visid_incap", "nlbi_", "imperva", "incapsula"})

# Cloudflare error code patterns
_CF_ERROR_PATTERNS = {
    1009: ("geo", "Access denied — geo-block (Error 1009)"),
    1010: ("tls", "Access denied — TLS/HTTP2 fingerprint mismatch (Error 1010)"),
    1015: ("rate", "Rate limited (Error 1015)"),
    1020: ("waf", "Access denied — WAF/bot behavior block (Error 1020)"),
}

# Honeypot / AI-Labyrinth detection
_HONEYPOT_SIZE_THRESHOLD = 50_000  # suspiciously large filler page
# Fallback bound when no expected selectors are known: without content
# markers only size is available, so require an order of magnitude above
# the base threshold before a legitimate-looking page is suspected.
_HONEYPOT_SIZE_FALLBACK = 200_000


def _normalize_headers(headers: dict[str, str] | None) -> dict[str, str]:
    return {k.lower(): v.lower() for k, v in (headers or {}).items()}


def _get_cookies_for_host(url: str) -> set[str]:
    """Return the cookie names currently stored for ``url``'s host.

    Set-Cookie headers from prior responses are already absorbed into the jar
    by :func:`comic_dl.http.absorb_response_cookies`, so the jar alone is the
    authoritative view of cookies the client would send to this host.
    """
    names: set[str] = set()
    host = urlsplit(url).hostname
    if not host:
        return names
    jar = get_jar()
    if jar is not None:
        for row in jar.list(host):
            name = row.get("name")
            if name:
                names.add(name.lower())
    return names


def _extract_cf_error_code(body: str) -> int | None:
    """Extract Cloudflare error code from challenge page."""
    # Look for "Error 1009", "Error 1010", etc. in title or body
    m = re.search(r"[Ee]rror\s+(10\d{2})", body)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    # Look for ray ID + error code in JSON embedded in page
    m = re.search(r'"error"\s*:\s*(\d{4})', body)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


def _extract_cf_challenge_type(body: str) -> str | None:
    """Extract Cloudflare challenge type from interstitial body."""
    # Look for _cf_chl_opt.cType
    m = re.search(r'_cf_chl_opt\s*[=:]\s*\{[^}]*"cType"\s*:\s*"([^"]+)"', body)
    if m:
        ctype = m.group(1).lower()
        if ctype in {"non-interactive", "managed", "interactive"}:
            return ctype
    # Check for Turnstile
    if "cf-turnstile" in body.lower() or "turnstile-widget" in body.lower():
        return "turnstile"
    # Check for "I'm Under Attack" mode
    if "under attack" in body.lower() or "checking your browser" in body.lower():
        return "under-attack"
    # Check for Bot Management cookie
    if "__cf_bm" in body:
        return "__cf_bm"
    return None


def _classify_captcha_vendor(body: str) -> tuple[str | None, str | None]:
    """Return (vendor, kind) for CAPTCHA vendors if detected."""
    body_lower = body.lower()

    # reCAPTCHA v3 (score-based) - check first since v2 also contains api.js
    if any(m in body_lower for m in _RECAPTCHA_V3_MARKERS):
        return "recaptcha", "score"

    # Turnstile before reCAPTCHA v2: both widgets use a `data-sitekey`
    # attribute, so a Turnstile host must not fall through to the v2 branch.
    if any(m in body_lower for m in _TURNSTILE_MARKERS):
        # Turnstile can be interactive (checkbox) or non-interactive
        # Heuristic: if there's a widget/div, it's interactive
        if "turnstile-widget" in body_lower or "data-sitekey" in body_lower:
            return "turnstile", "puzzle"
        return "turnstile", "non-interactive"

    # hCaptcha (puzzle)
    if any(m in body_lower for m in _HCAPTCHA_MARKERS):
        return "hcaptcha", "puzzle"

    # reCAPTCHA v2 (puzzle) - requires a g-recaptcha marker specifically;
    # a bare data-sitekey alone (captcha/turnstile share that attribute)
    # must not be read as reCAPTCHA v2.
    if any(m in body_lower for m in _RECAPTCHA_V2_MARKERS):
        return "recaptcha", "puzzle"

    return None, None


def _detect_waf_vendor(headers: dict[str, str], cookies: set[str], body: str) -> str | None:
    """Detect non-CF WAF vendor from headers, cookies, body.

    ``headers`` must already be lowercased (as returned by
    :func:`_normalize_headers`).
    """
    h = headers
    body_lower = body.lower()

    # Check cookies first (most reliable)
    for c in cookies:
        if any(m in c for m in _DATADOME_MARKERS):
            return "datadome"
        if any(m in c for m in _AKAMAI_MARKERS):
            return "akamai"
        if any(m in c for m in _PERIMETERX_MARKERS):
            return "perimeterx"
        if any(m in c for m in _KASADA_MARKERS):
            return "kasada"
        if any(m in c for m in _IMPERVA_MARKERS):
            return "imperva"

    # Check headers
    for key, val in h.items():
        if any(m in key or m in val for m in _DATADOME_MARKERS):
            return "datadome"
        if any(m in key or m in val for m in _AKAMAI_MARKERS):
            return "akamai"
        if any(m in key or m in val for m in _PERIMETERX_MARKERS):
            return "perimeterx"
        if any(m in key or m in val for m in _KASADA_MARKERS):
            return "kasada"
        if any(m in key or m in val for m in _IMPERVA_MARKERS):
            return "imperva"

    # Check body for JS markers
    if any(m in body_lower for m in _DATADOME_MARKERS):
        return "datadome"
    if any(m in body_lower for m in _AKAMAI_MARKERS):
        return "akamai"
    if any(m in body_lower for m in _PERIMETERX_MARKERS):
        return "perimeterx"
    if any(m in body_lower for m in _KASADA_MARKERS):
        return "kasada"
    if any(m in body_lower for m in _IMPERVA_MARKERS):
        return "imperva"

    return None


def _is_honeypot(body: str, expected_selectors: list[str] | None = None) -> bool:
    """Heuristic: 200-OK page that lacks expected content markers.

    AI Labyrinth (2025+) serves large filler pages that look legitimate but
    contain no actionable content. We flag when:
    - Status is 200
    - Body is suspiciously large (>50KB, or >200KB with no expected selectors)
    - Expected selectors (provided by caller) are missing
    """
    if len(body) < _HONEYPOT_SIZE_THRESHOLD:
        return False
    if expected_selectors:
        return all(sel not in body for sel in expected_selectors)
    # No expected selectors provided — fall back to size alone: without
    # content markers filler cannot be told apart from content, so only the
    # most extreme size is suspicious. Structural-entropy checks are not a
    # reliable signal here and are intentionally not attempted.
    return len(body) > _HONEYPOT_SIZE_FALLBACK


def classify_block(
    status: int,
    headers: dict[str, str] | None,
    body: str = "",
    url: str = "",
    expected_selectors: list[str] | None = None,
) -> BlockVerdict:
    """Classify a blocked/challenged response into a BlockVerdict.

    Args:
        status: HTTP status code
        headers: Response headers (case-insensitive keys)
        body: Response body (truncated to ~256KB for performance)
        url: Request URL (for cookie jar lookup)
        expected_selectors: DOM IDs/selectors the caller expects on a valid page

    Returns:
        BlockVerdict with vendor, kind, reason, challenge subtype, honeypot flag
    """
    h = _normalize_headers(headers)
    body_lower = body[:256_000].lower()
    cookies = _get_cookies_for_host(url)

    trace(f"antibot: classifying {status} for {urlsplit(url).hostname or 'unknown'}")

    # 1. Cloudflare detection (most common)
    server = h.get("server", "").strip()
    if status in (403, 503) and server in _CF_SERVER:
        cf_error = _extract_cf_error_code(body)
        cf_challenge = _extract_cf_challenge_type(body)
        reason = None
        if cf_error and cf_error in _CF_ERROR_PATTERNS:
            kind, reason = _CF_ERROR_PATTERNS[cf_error]
        else:
            kind = "interstitial"

        honeypot = _is_honeypot(body, expected_selectors)
        # Recommended action for CF challenges
        if kind == "geo":
            action = "impersonation"  # Geo-blocks can't be solved
        elif kind == "tls":
            action = "impersonation"  # TLS fingerprint mismatch
        elif kind == "rate":
            action = "rate-limit"  # Rate limited
        elif cf_challenge in ("interactive", "managed"):
            action = "webview"  # Interactive challenges need webview
        else:
            action = "retry"  # Non-interactive, try again
        return BlockVerdict(
            vendor="cloudflare",
            kind=kind,
            reason=reason,
            challenge=cf_challenge,
            honeypot=honeypot,
            recommended_action=action,
        )

    # CF via cf-mitigated header
    if h.get("cf-mitigated") == "challenge":
        cf_challenge = _extract_cf_challenge_type(body)
        return BlockVerdict(
            vendor="cloudflare",
            kind="interstitial",
            reason="Cloudflare challenge (cf-mitigated header)",
            challenge=cf_challenge,
            honeypot=_is_honeypot(body, expected_selectors),
        )

    # CF via body markers
    if status in (403, 503) and any(m in body_lower for m in _CF_BODY_MARKERS):
        cf_challenge = _extract_cf_challenge_type(body)
        return BlockVerdict(
            vendor="cloudflare",
            kind="interstitial",
            reason="Cloudflare challenge (body markers)",
            challenge=cf_challenge,
            honeypot=_is_honeypot(body, expected_selectors),
        )

    # 2. CAPTCHA vendors (may appear on 200 or 403)
    captcha_vendor, captcha_kind = _classify_captcha_vendor(body)
    if captcha_vendor and captcha_kind:
        honeypot = _is_honeypot(body, expected_selectors)
        # CAPTCHA challenges need webview or manual solve
        action = "webview" if captcha_kind == "puzzle" else "impersonation"
        return BlockVerdict(
            vendor=captcha_vendor,
            kind=captcha_kind,
            reason=f"{captcha_vendor} challenge ({captcha_kind})",
            challenge=captcha_kind,
            honeypot=honeypot,
            recommended_action=action,
        )

    # 3. Non-CF WAF vendors
    waf_vendor = _detect_waf_vendor(h, cookies, body)
    if waf_vendor:
        kind = "waf"
        reason = f"{waf_vendor} WAF challenge/block"
        action = "retry"  # Default: try again with impersonation
        if status == 403:
            reason += " (403 Forbidden)"
        elif status == 429:
            kind = "rate"
            reason += " (429 Too Many Requests)"
            action = "rate-limit"
        return BlockVerdict(
            vendor=waf_vendor,
            kind=kind,
            reason=reason,
            challenge=None,
            honeypot=_is_honeypot(body, expected_selectors),
            recommended_action=action,
        )

    # 4. Generic blocks with no identifiable vendor
    if status == 403:
        return BlockVerdict(
            vendor="unknown",
            kind="waf",
            reason="Forbidden (403) — unknown WAF or server config",
            challenge=None,
            honeypot=_is_honeypot(body, expected_selectors),
            recommended_action="retry",
        )
    if status == 429:
        return BlockVerdict(
            vendor="unknown",
            kind="rate",
            reason="Rate limited (429)",
            challenge=None,
            honeypot=_is_honeypot(body, expected_selectors),
            recommended_action="rate-limit",
        )
    if status >= 500:
        return BlockVerdict(
            vendor="unknown",
            kind="waf",
            reason=f"Server error ({status})",
            challenge=None,
            honeypot=_is_honeypot(body, expected_selectors),
            recommended_action="retry",
        )

    # 5. Honeypot check for 200-OK (AI Labyrinth)
    if status == 200 and _is_honeypot(body, expected_selectors):
        return BlockVerdict(
            vendor="cloudflare",  # AI Labyrinth is a CF feature
            kind="honeypot",
            reason="Suspected AI Labyrinth filler page (200 OK, no expected content)",
            challenge=None,
            honeypot=True,
            recommended_action="webview",  # Honeypots need webview to bypass
        )

    # 6. No challenge detected
    return BlockVerdict(
        vendor="none",
        kind="none",
        reason=None,
        challenge=None,
        honeypot=False,
    )


# Backwards-compatible thin predicate used by retry_challenge_once
def looks_like_challenge(
    status: int,
    headers: dict[str, str] | None,
    body: str = "",
) -> bool:
    """True when a response is a Cloudflare interstitial worth retrying.

    Mirrors the original behavior: status 403/503 + CF markers.
    Re-implemented on top of classify_block() for consistency.
    """
    if status not in (403, 503):
        return False
    h = _normalize_headers(headers)
    server = h.get("server", "").strip()
    if server in _CF_SERVER:
        return True
    if h.get("cf-mitigated") == "challenge":
        return True
    body_lower = body[:256_000].lower()
    return any(m in body_lower for m in _CF_BODY_MARKERS)
