"""``comic-dl self`` — detect the installation source and update through its owner.

Updates never guess. OS-package installs are updated by the owning package
manager from a freshly downloaded package artifact, pip/uv installs by the
tool that created them, source checkouts are left for ``git``, and any
installation mode we cannot identify confidently is reported instead of
acted on.

The outbound fetches reuse the scrapers' shared fetch/stream helpers
(:meth:`BaseScraper._timeout_get`, :func:`downloader._open_stream`), so every
hop is validated against SSRF targets, paced by the per-host rate limiter, and
bounded by the standard timeouts. A lookup failure never touches the download
path — it only affects this command.
"""

from __future__ import annotations

import datetime
import enum
import hashlib
import hmac
import json
import os
import platform
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from curl_cffi.requests import AsyncSession
from rich.prompt import Confirm

from . import __version__ as _version
from .downloader import _open_stream
from .errors import EXIT_ERROR, EXIT_INTERRUPTED, EXIT_OK
from .scrapers.base import BaseScraper
from .ui import (
    DIAGNOSTIC,
    TAG_HTTP,
    get_ui_gate,
    print_dim,
    print_error,
    print_success,
    print_warning,
    run_with_status,
    vlog,
)
from .utils import http_client_args

GITHUB_API_RELEASES_LATEST = "https://api.github.com/repos/fallen020/comic-dl/releases/latest"
GITHUB_RELEASES_PAGE = "https://github.com/fallen020/comic-dl/releases"

#: Package artifacts ship per release; extension + arch select the right one.
_ARCH_SUFFIX = "-x86_64."
_PACMAN_SUFFIX = ".pkg.tar.zst"
_MAX_ARTIFACT_BYTES = 200 * 1024 * 1024


class InstallKind(enum.Enum):
    """How comic-dl is installed, and therefore who owns an update."""

    APT = "apt"
    RPM = "rpm"
    PACMAN = "pacman"
    PIP = "pip"
    UV = "uv"
    SOURCE = "source"
    BINARY = "binary"
    UNKNOWN = "unknown"


#: Human labels for each install source, shown in ``self update`` output.
_INSTALL_LABELS = {
    InstallKind.APT: "apt (Debian/Ubuntu)",
    InstallKind.RPM: "rpm (Fedora/RHEL)",
    InstallKind.PACMAN: "pacman (Arch)",
    InstallKind.PIP: "pip environment",
    InstallKind.UV: "uv tool",
    InstallKind.SOURCE: "source checkout",
    InstallKind.BINARY: "standalone executable",
    InstallKind.UNKNOWN: "unknown",
}


def _install_label(kind: InstallKind) -> str:
    return _INSTALL_LABELS.get(kind, kind.value)


@dataclass(frozen=True)
class InstallationInfo:
    """Result of :func:`detect_installation`."""

    kind: InstallKind
    executable: Path
    location: Path | None = None
    writable: bool = False


@dataclass(frozen=True)
class ReleaseInfo:
    """A published GitHub release and its asset map (name -> url)."""

    tag: str
    version: str
    assets: dict[str, str]


class ReleaseFailureKind(enum.Enum):
    """Why the latest-release lookup failed (each renders one user line)."""

    OFFLINE = "offline"
    RATE_LIMITED = "rate_limited"
    NOT_FOUND = "not_found"
    MALFORMED = "malformed"
    SERVER_ERROR = "server_error"


@dataclass(frozen=True)
class ReleaseFailure:
    """A failed latest-release lookup: machine-readable cause + detail.

    ``detail`` carries the rate-limit reset ("14:02 UTC") or the HTTP
    status for server errors; empty otherwise.
    """

    kind: ReleaseFailureKind
    detail: str = ""


def compare_versions(installed: str, latest: str) -> int:
    """Order two version strings (-1 / 0 / 1).

    Tags follow PEP 440 ``vX.Y.Z`` with no prerelease suffixes (enforced by
    the release policy), so an integer-tuple compare is exact today. A local
    prerelease build sorts below the release with the same number, so it
    never reports "up to date" against a published version.
    """
    return (
        1
        if _version_key(installed) > _version_key(latest)
        else -1
        if _version_key(installed) < _version_key(latest)
        else 0
    )


def parse_version(raw: str) -> tuple[int, ...]:
    """Numeric dot-parts of ``raw`` (trailing prerelease text ignored)."""
    s = raw.strip().lstrip("vV= ")
    out: list[int] = []
    for piece in s.split("."):
        digits = ""
        for ch in piece:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


def _version_key(raw: str) -> tuple[tuple[int, ...], int]:
    """Sort key: (release parts, 1 for a release, 0 for a prerelease).

    The prerelease flag compares only after the numeric parts match — a dev
    build of ``0.0.2`` therefore sits below the published ``0.0.2`` instead
    of being pinned to it.
    """
    s = raw.strip().lstrip("vV= ")
    prerelease = False
    for piece in s.split("."):
        digits = ""
        for ch in piece:
            if ch.isdigit():
                digits += ch
            else:
                break
        if len(digits) < len(piece):
            prerelease = True
        if not digits:
            prerelease = True
            break
    return (parse_version(raw), 0 if prerelease else 1)


def detect_installation() -> InstallationInfo:
    """Layered detection: frozen, OS package owner, editable, uv, pip.

    OS-package ownership is authoritative (a lost-package entry in the host
    package manager overrides every other signal). Detection never needs
    privileges: the ``<tool> -S/-Q <path>`` file-ownership queries run as
    the current user and each tool is only invoked when present on PATH.
    """
    exe = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return InstallationInfo(InstallKind.BINARY, exe, writable=_writable(exe.parent))

    script = _console_script() or exe
    owner = _os_package_owner(script)
    if owner is not None:
        return InstallationInfo(owner, script)

    dist = _distribution()
    if dist is None:
        return InstallationInfo(InstallKind.UNKNOWN, script)

    location = _dist_location(dist)
    if _is_editable(dist) or _looks_like_checkout(location):
        return InstallationInfo(
            InstallKind.SOURCE,
            script,
            location=location,
            writable=_writable(location) if location else False,
        )

    if _uv_tool_install(exe):
        return InstallationInfo(
            InstallKind.UV, exe, location=location, writable=_writable(exe.parent)
        )

    if sys.prefix != sys.base_prefix:
        return InstallationInfo(
            InstallKind.PIP,
            script,
            location=location,
            writable=_writable(Path(sys.prefix)),
        )
    if _user_site_writable():
        return InstallationInfo(InstallKind.PIP, script, location=location, writable=True)

    # System Python (prefix == base_prefix, not a user install): pip here may
    # need privileges we must not assume. Report rather than guess means the
    # update path instructs instead of risking a half-managed environment.
    return InstallationInfo(InstallKind.PIP, script, location=location, writable=False)


def select_asset(release: ReleaseInfo, kind: InstallKind) -> tuple[str, str] | None:
    """Pick the package asset URL for ``kind`` on this machine, or ``None``.

    Returns ``(asset_name, url)``. Architecture is matched in the asset name
    since the release ships one package per distro per arch (the machine
    reports ``x86_64`` where Debian names the arch ``amd64``). An arch with no
    published asset (pacman arm64) yields ``None`` so the caller instructs.
    """
    arch_map = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    arch = arch_map.get((platform.machine() or "").lower())
    if arch is None:
        return None
    suffix: str | None = None
    if kind is InstallKind.APT:
        suffix = f"_{arch}.deb"
    elif kind is InstallKind.RPM:
        rpm_arch = "x86_64" if arch == "amd64" else "aarch64"
        suffix = f".{rpm_arch}.rpm"
    elif kind is InstallKind.PACMAN:
        suffix = _PACMAN_SUFFIX if arch == "amd64" else None
    else:
        return None
    if suffix is None:
        return None
    candidates = {
        name: url
        for name, url in release.assets.items()
        if name.endswith(suffix) and (kind is not InstallKind.PACMAN or _ARCH_SUFFIX in name)
    }
    if not candidates:
        return None
    name = sorted(candidates)[0]
    return name, candidates[name]


def install_args(kind: InstallKind, artifact: Path) -> list[str]:
    """Arg list that installs a downloaded artifact via the owning package
    manager. ``--yes``/``--noconfirm`` keep non-interactive installs moving."""
    if kind is InstallKind.APT:
        return ["apt", "install", "--yes", str(artifact)]
    if kind is InstallKind.RPM:
        return ["dnf", "install", "--yes", str(artifact)]
    if kind is InstallKind.PACMAN:
        return ["pacman", "-U", "--noconfirm", str(artifact)]
    raise ValueError(f"no package-manager install for {kind.name}")


async def fetch_latest_release() -> ReleaseInfo | ReleaseFailure:
    """The latest published release, or a :class:`ReleaseFailure`.

    Transport errors, rate limits, and malformed payloads no longer
    collapse: the caller renders one line per cause, and the cause
    itself is logged at ``-vv`` without leaking exception details.
    """
    try:
        async with AsyncSession(**http_client_args(host="api.github.com")) as client:
            resp = await BaseScraper._timeout_get(
                GITHUB_API_RELEASES_LATEST, client, use_cache=True
            )
    except Exception as exc:
        vlog(DIAGNOSTIC, f"release lookup failed: {type(exc).__name__}", tag=TAG_HTTP)
        return ReleaseFailure(ReleaseFailureKind.OFFLINE)
    if resp.status_code == 429 or (
        resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0"
    ):
        vlog(DIAGNOSTIC, f"release lookup rate limited (HTTP {resp.status_code})", tag=TAG_HTTP)
        return ReleaseFailure(
            ReleaseFailureKind.RATE_LIMITED, detail=_rate_limit_reset(resp.headers)
        )
    if resp.status_code == 404:
        vlog(DIAGNOSTIC, "release lookup 404", tag=TAG_HTTP)
        return ReleaseFailure(ReleaseFailureKind.NOT_FOUND)
    if resp.status_code != 200:
        vlog(DIAGNOSTIC, f"release lookup HTTP {resp.status_code}", tag=TAG_HTTP)
        return ReleaseFailure(ReleaseFailureKind.SERVER_ERROR, detail=str(resp.status_code))
    try:
        data = resp.json()
    except (ValueError, TypeError, json.JSONDecodeError):
        vlog(DIAGNOSTIC, "release lookup returned malformed JSON", tag=TAG_HTTP)
        return ReleaseFailure(ReleaseFailureKind.MALFORMED)
    if not isinstance(data, dict):
        vlog(DIAGNOSTIC, "release lookup returned malformed JSON", tag=TAG_HTTP)
        return ReleaseFailure(ReleaseFailureKind.MALFORMED)
    tag = str(data.get("tag_name") or "")
    if not tag:
        vlog(DIAGNOSTIC, "release lookup response has no tag", tag=TAG_HTTP)
        return ReleaseFailure(ReleaseFailureKind.MALFORMED)
    assets: dict[str, str] = {}
    for item in data.get("assets") or []:
        name = (item or {}).get("name")
        url = (item or {}).get("browser_download_url")
        if name and url:
            assets[str(name)] = str(url)
    # Tags are ``vX.Y.Z``; display and compare the bare ``X.Y.Z`` form so
    # output never mixes prefix styles.
    return ReleaseInfo(tag=tag, version=tag.lstrip("vV"), assets=assets)


def _rate_limit_reset(headers: Any) -> str:
    """Human reset time from rate-limit headers ("" when absent).

    Prefers ``retry-after`` seconds, else the ``x-ratelimit-reset`` epoch.
    Absolute UTC keeps the message deterministic and timezone-explicit.
    """
    try:
        after = int(headers.get("retry-after") or 0)
    except (TypeError, ValueError):
        after = 0
    if after > 0:
        epoch: int | float = time.time() + after
    else:
        try:
            epoch = int(headers.get("x-ratelimit-reset") or 0)
        except (TypeError, ValueError):
            return ""
        if epoch <= 0:
            return ""
    return datetime.datetime.fromtimestamp(epoch, datetime.UTC).strftime("%H:%M UTC")


def report_release_failure(failure: ReleaseFailure) -> None:
    """One error line + one hint for a failed release lookup.

    Stderr-safe under ``--json`` (``print_error`` always targets stderr;
    ``print_dim`` reroutes there in JSON mode).
    """
    kind = failure.kind
    if kind is ReleaseFailureKind.RATE_LIMITED:
        reset = failure.detail or "an unknown time"
        print_error(f"GitHub API rate limit reached; resets at {reset}.")
        print_dim(f"Latest releases: {GITHUB_RELEASES_PAGE}")
    elif kind is ReleaseFailureKind.NOT_FOUND:
        print_error(f"GitHub API has no published release at {GITHUB_API_RELEASES_LATEST}.")
        print_dim(f"Latest releases: {GITHUB_RELEASES_PAGE}")
    elif kind is ReleaseFailureKind.MALFORMED:
        print_error("GitHub API returned an unexpected response.")
        print_dim(f"Latest releases: {GITHUB_RELEASES_PAGE}")
    elif kind is ReleaseFailureKind.SERVER_ERROR:
        print_error(f"GitHub API error (HTTP {failure.detail or 'unknown'}).")
        print_dim(f"Latest releases: {GITHUB_RELEASES_PAGE}")
    else:
        print_error("Unable to check for updates: no network connection.")
        print_dim("Check your network connection, or configure --proxy.")


async def download_artifact(
    url: str, dest: Path, *, on_progress: Callable[[int, int | None], None] | None = None
) -> None:
    """Stream a release asset to ``dest`` with the shared validated streamer.

    ``on_progress(done, total)`` reports cumulative bytes (total ``None``
    when the server hides its length); UI stays with the caller so this
    remains unit-testable without a console.
    """
    size = 0
    async with AsyncSession(**http_client_args()) as client:
        resp = await _open_stream(client, url)
        try:
            if resp.status_code >= 400:
                raise OSError(f"HTTP {resp.status_code}")
            total: int | None = None
            if on_progress is not None:
                try:
                    total = int(resp.headers.get("content-length") or 0) or None
                except (TypeError, ValueError):
                    total = None
            with dest.open("wb") as fh:
                async for chunk in resp.aiter_content(1 << 16):
                    size += len(chunk)
                    if size > _MAX_ARTIFACT_BYTES:
                        raise OSError("release asset exceeds size cap")
                    fh.write(chunk)
                    if on_progress is not None:
                        on_progress(size, total)
        finally:
            await resp.aclose()


_MAX_CHECKSUMS_BYTES = 1 << 20


def _parse_checksums(text: str) -> dict[str, str]:
    """Filename → sha256 from SHA256SUMS text; malformed lines skipped."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        digest, name = parts
        name = name.lstrip("*")
        if len(digest) == 64 and all(c in "0123456789abcdefABCDEF" for c in digest):
            out[name] = digest.lower()
    return out


async def _fetch_checksums(release: ReleaseInfo) -> dict[str, str]:
    """The release's SHA256SUMS as filename → sha256, or ``{}`` on any failure.

    A missing/unreadable checksum file fails closed: the caller refuses to
    install rather than installing an unverified artifact as root.
    """
    url = release.assets.get("SHA256SUMS")
    if not url:
        return {}
    try:
        async with AsyncSession(**http_client_args()) as client:
            resp = await BaseScraper._timeout_get(url, client, use_cache=False)
        if resp.status_code >= 400 or len(resp.text) > _MAX_CHECKSUMS_BYTES:
            return {}
        return _parse_checksums(resp.text)
    except Exception:
        return {}


def _verify_artifact(checksums: dict[str, str], name: str, path: Path) -> bool:
    """True when ``path`` matches the published sha256 for ``name``."""
    expected = checksums.get(name)
    if expected is None:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return hmac.compare_digest(digest.hexdigest(), expected)


async def run_update_command(*, check: bool, yes: bool, quiet: bool = False) -> int:
    """``comic-dl self update``: check, then update through the owner.

    Returns an exit code. ``check=True`` never modifies anything. Confirmations
    are skipped with ``yes``; a non-interactive run without ``--yes`` refuses
    (mirrors ``cookie clear``), and every failure falls back to exact manual
    instructions.
    """
    info = detect_installation()
    print_dim(f"Installed via:        {_install_label(info.kind)}")
    print_dim(f"Installed version:    {_version}")

    # Spinner covers the lookup only: prompts and installs below must run
    # without a live renderer. The gate is held here, so no caller may
    # hold it (a second acquire from the same task would deadlock).
    if quiet:
        release = await fetch_latest_release()
    else:
        await get_ui_gate().acquire()
        try:
            release = await run_with_status("Checking for updates", fetch_latest_release())
        finally:
            get_ui_gate().release()
    if isinstance(release, ReleaseFailure):
        print_dim("Latest version:       unknown")
        report_release_failure(release)
        return EXIT_ERROR

    latest = release.version
    print_dim(f"Latest version:       {latest}")
    if compare_versions(_version, latest) >= 0:
        print_success("Status:              already up to date.")
        return EXIT_OK
    print_warning(f"Status:              update available ({_version} → {latest}).")
    print_dim(f"Release notes:        {GITHUB_RELEASES_PAGE}/tag/{release.tag}")

    if check:
        return EXIT_OK

    if info.kind is InstallKind.SOURCE:
        print_dim("comic-dl is running from a source checkout.")
        print_dim("Update it with:")
        print_dim("  git pull")
        print_dim("  uv sync")
        return EXIT_OK
    if info.kind is InstallKind.BINARY:
        print_dim(f"Download the new release: {GITHUB_RELEASES_PAGE}")
        return EXIT_OK
    if info.kind is InstallKind.UNKNOWN:
        print_error("Could not identify how comic-dl was installed.")
        print_dim(f"Reinstall from: {GITHUB_RELEASES_PAGE}")
        return EXIT_ERROR
    if info.kind is InstallKind.PIP:
        return _update_pip(info, latest, yes)
    if info.kind is InstallKind.UV:
        return _update_uv(latest, yes)

    blocked = _request_confirmation(
        yes,
        f"Install comic-dl {latest} via {info.kind.value} (requires sudo)?",
        says_interactive_no="Update skipped.",
    )
    if blocked is not None:
        return blocked
    return await _install_package(info, release, latest, quiet=quiet)


def _update_pip(info: InstallationInfo, latest: str, yes: bool) -> int:
    if not info.writable:
        print_error("This environment is not writable.")
        print_dim(
            "Reinstall in a virtual environment, or run with the tool that "
            f"manages it:  {sys.executable} -m pip install --upgrade comic-dl"
        )
        return EXIT_ERROR
    blocked = _request_confirmation(
        yes,
        f"Update comic-dl {_version} → {latest} with pip?",
        says_interactive_no="Update skipped.",
    )
    if blocked is not None:
        return blocked
    argv = [sys.executable, "-m", "pip", "install", "--upgrade", "comic-dl"]
    rc = subprocess.run(argv).returncode  # nosec B603
    if rc == 0:
        print_success(f"Updated to comic-dl {latest}.")
        return EXIT_OK
    print_error(f"pip upgrade failed (exit code {rc}).")
    print_dim(f"Run it yourself: {' '.join(argv)}")
    return EXIT_ERROR


def _update_uv(latest: str, yes: bool) -> int:
    blocked = _request_confirmation(
        yes,
        f"Update comic-dl {_version} → {latest} with uv?",
        says_interactive_no="Update skipped.",
    )
    if blocked is not None:
        return blocked
    argv = ["uv", "tool", "upgrade", "comic-dl"]
    rc = subprocess.run(argv).returncode  # nosec B603
    if rc == 0:
        print_success(f"Updated to comic-dl {latest}.")
        return EXIT_OK
    print_error(f"uv upgrade failed (exit code {rc}).")
    print_dim(f"Run it yourself: {' '.join(argv)}")
    return EXIT_ERROR


async def _install_package(
    info: InstallationInfo, release: ReleaseInfo, latest: str, *, quiet: bool = False
) -> int:
    """Download the matching package artifact and hand it to a privileged
    install; every failure instructs instead of leaving the system touched."""
    asset = select_asset(release, info.kind)
    if asset is None:
        arch = (platform.machine() or "").lower()
        print_error(f"No {info.kind.value} package is published for {arch}.")
        print_dim(f"Pick a matching asset from: {GITHUB_RELEASES_PAGE}")
        return EXIT_ERROR

    name, url = asset
    tmp_root = Path(tempfile.mkdtemp(prefix="comic-dl-self-"))
    artifact = tmp_root / name
    from .ui import Pipeline, format_bytes_fixed

    # Bar once the length is known, spinner text while it is not.
    async with Pipeline(quiet=quiet) as pipe:
        pipe.stage(f"Downloading {name}...")
        shown_total: int | None = None
        shown = ""

        def on_progress(done: int, total: int | None) -> None:
            nonlocal shown, shown_total
            if total and total != shown_total:
                shown_total = total
                pipe.show_progress(total, unit="bytes")
            if shown_total:
                pipe.update_progress(done)
            else:
                text = f"Downloading {name} ({format_bytes_fixed(done)})"
                if text != shown:
                    shown = text
                    pipe.set_activity(text)

        try:
            await download_artifact(url, artifact, on_progress=on_progress)
        except Exception:
            print_error(f"Failed to download {name}.")
            print_dim(f"Latest releases: {GITHUB_RELEASES_PAGE}")
            _cleanup(tmp_root)
            return EXIT_ERROR

    checksums = await _fetch_checksums(release)
    if name not in checksums:
        print_error(f"No checksum published for {name} — refusing to install.")
        print_dim(f"Install manually from: {GITHUB_RELEASES_PAGE}")
        _cleanup(tmp_root)
        return EXIT_ERROR
    if not _verify_artifact(checksums, name, artifact):
        print_error(f"Checksum mismatch for {name} — refusing to install.")
        print_dim(f"Install manually from: {GITHUB_RELEASES_PAGE}")
        _cleanup(tmp_root)
        return EXIT_ERROR
    print_success(f"Checksum verified (sha256): {name}.")

    is_root = _is_root()
    if is_root:
        argv = install_args(info.kind, artifact)
    else:
        sudo = shutil.which("sudo")
        if sudo is None:
            print_error("sudo is required to install the package.")
            print_dim(f"Install manually from: {GITHUB_RELEASES_PAGE}")
            _cleanup(tmp_root)
            return EXIT_ERROR
        argv = [sudo, *install_args(info.kind, artifact)]

    print_dim(f"Running: {' '.join(argv)}")
    rc = subprocess.run(argv).returncode  # nosec B603
    _cleanup(tmp_root)
    if rc == 0:
        print_success(f"Installed comic-dl {latest}.")
        return EXIT_OK
    print_error(f"The {info.kind.value} install failed (exit code {rc}).")
    print_dim(f"Run it yourself: {' '.join(argv)}")
    return EXIT_ERROR


def _request_confirmation(yes: bool, question: str, *, says_interactive_no: str) -> int | None:
    """Exit code to return because confirmation was absent/refused, else None.

    ``yes`` skips the prompt. A non-interactive run without ``--yes`` refuses
    outright (the ``cookie clear`` convention: never silently proceed in an
    unattended prompt), while an interactive ``no`` returns a plain skip.
    """
    if yes:
        return None
    if not _is_interactive():
        print_error("Updating requires confirmation.")
        print_dim("Re-run with -y/--yes to proceed without a prompt.")
        return EXIT_INTERRUPTED
    if not Confirm.ask(question, default=False):
        print_dim(says_interactive_no)
        return EXIT_OK
    return None


def _console_script() -> Path | None:
    """Resolved path of the ``comic-dl`` script on PATH, if any."""
    found = shutil.which("comic-dl")
    if not found:
        return None
    try:
        return Path(found).resolve()
    except OSError:
        return None


def _os_package_owner(script: Path) -> InstallKind | None:
    """The host package manager owning ``script``, or ``None``.

    Positive file-ownership answers from ``dpkg``/``rpm``/``pacman`` are
    authoritative for the OS-managed install row in the matrix.
    """
    if os.name == "nt":
        return None
    probes = (
        ("dpkg-query", InstallKind.APT),
        ("rpm", InstallKind.RPM),
        ("pacman", InstallKind.PACMAN),
    )
    for tool, kind in probes:
        path = shutil.which(tool)
        if path is None or not script.exists():
            continue
        flag = "-S" if tool == "dpkg-query" else ("-qf" if tool == "rpm" else "-Qo")
        try:
            rc = subprocess.run(  # nosec B603
                [path, flag, str(script)],
                capture_output=True,
            ).returncode
        except OSError:
            continue
        if rc == 0:
            return kind
    return None


def _distribution() -> Any | None:
    from importlib.metadata import PackageNotFoundError, distribution

    try:
        return distribution("comic-dl")
    except PackageNotFoundError:
        return None


def _dist_location(dist: Any) -> Path | None:
    try:
        return Path(dist.locate())
    except Exception:
        return None


def _is_editable(dist: Any) -> bool:
    try:
        raw = dist.read_text("direct_url.json")
    except Exception:
        return False
    try:
        return json.loads(raw).get("dir_info", {}).get("editable", False)
    except (ValueError, TypeError, AttributeError):
        return False


def _looks_like_checkout(location: Path | None) -> bool:
    """True when ``location`` sits inside the project checkout (belt for
    editable installs whose direct_url metadata is absent)."""
    if location is None:
        return False
    current = location
    for _ in range(6):
        if (current / ".git").exists() and (current / "pyproject.toml").exists():
            return True
        if current.parent == current:
            break
        current = current.parent
    return False


def _uv_tool_install(exe: Path) -> bool:
    tools = os.environ.get("UV_TOOL_DIR")
    root = Path(tools).expanduser() if tools else Path.home() / ".local/share/uv/tools"
    try:
        exe.relative_to(root)
        return True
    except ValueError:
        return False


def _user_site_writable() -> bool:
    import site as _site

    return _writable(Path(_site.getusersitepackages()))


def _writable(path: Path) -> bool:
    return os.access(path, os.W_OK)


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _is_interactive() -> bool:
    from .ui import is_interactive

    return is_interactive()


def _cleanup(root: Path) -> None:
    shutil.rmtree(root, ignore_errors=True)
