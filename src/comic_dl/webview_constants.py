"""Tunables shared by the webview solver subprocess and its parent.

The parent's subprocess deadline (``SOLVE_TIMEOUT``/``SESSION_TIMEOUT``) and
the helper's own challenge/poll timers (``COOKIE_TIMEOUT``/``POLL_INTERVAL``)
describe one thing from both sides of the pipe: how long a Cloudflare
challenge may take before the parent fails over to impersonation. Keeping
them here, rather than duplicated at each end, is what keeps the two sides
agreeing on that deadline.
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: How long the parent waits for a one-shot challenge solve before it fails
#: over to impersonation.
SOLVE_TIMEOUT = 60.0

#: How long a long-lived request-session helper may stay open.  This is the
#: *session lifetime* — not the per-request timeout, which is shorter.
SESSION_TIMEOUT = 300.0

#: Per-request timeout for individual in-page XHR calls.  Must be shorter
#: than ``SESSION_TIMEOUT`` so a single stuck request doesn't kill the session.
REQUEST_TIMEOUT = 30.0

#: How long the helper waits for a ``cf_clearance`` cookie to land.
COOKIE_TIMEOUT = 60.0

#: Poll interval while the helper waits for the clearance cookie.
POLL_INTERVAL = 0.5

#: Cookie name that marks a solved challenge.
CLEARANCE_NAME = "cf_clearance"

#: Hard cap on one protocol frame (JSON line) in bytes.  Protects against a
#: misbehaving helper flooding stdout.
MAX_FRAME_BYTES = 1 * 1024 * 1024

#: Maximum bytes to drain from the helper's stderr before discarding.
STDERR_DRAIN_BYTES = 4096


def origin_of(url: str) -> str:
    """Return the origin (scheme + host + port) of *url*."""
    s = urlsplit(url)
    port = s.port
    default_port = 443 if s.scheme == "https" else 80
    if port and port != default_port:
        return f"{s.scheme}://{s.hostname}:{port}"
    return f"{s.scheme}://{s.hostname}"
