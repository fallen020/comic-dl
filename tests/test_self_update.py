"""Offline tests for ``comic-dl self`` (version + installation-aware update).

No real network, package manager, pip, uv, sudo, or download is ever
involved: installs are faked with monkeypatched fixtures and subprocesses are
mocked, matching the repo-wide offline test convention.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest

from comic_dl import __version__ as _CORE_VERSION
from comic_dl.errors import EXIT_USAGE
from comic_dl.self_update import (
    EXIT_ERROR,
    EXIT_INTERRUPTED,
    EXIT_OK,
    InstallationInfo,
    InstallKind,
    ReleaseFailure,
    ReleaseFailureKind,
    ReleaseInfo,
    _request_confirmation,
    compare_versions,
    detect_installation,
    download_artifact,
    fetch_latest_release,
    install_args,
    parse_version,
    report_release_failure,
    run_update_command,
    select_asset,
)

# Functional install fixture location; never touches disk.
_MOCK_EXE = Path("/mock/comic-dl")

# Update-path fixtures need a release strictly newer than what's installed;
# deriving it keeps the tests independent of the current version number.


def _bump_patch(v: str) -> str:
    parts = [int(p) for p in v.split(".")]
    parts[-1] += 1
    return ".".join(str(p) for p in parts)


_FUTURE = _bump_patch(_CORE_VERSION)
_FUTURE_TAG = f"v{_FUTURE}"


def _rel(tag: str, *assets: str) -> ReleaseInfo:
    return ReleaseInfo(
        tag=tag,
        version=tag,
        assets={name: f"https://example.invalid/{name}" for name in assets},
    )


async def _checksums_for(mapping: dict[str, str]) -> dict[str, str]:
    return mapping


def _text(capsys) -> str:
    """Console + diagnostic output without Rich's folding newlines."""
    cap = capsys.readouterr()
    return (cap.out + cap.err).replace("\n", "")


class TestVersionParsing:
    def test_plain(self):
        assert parse_version("0.0.2") == (0, 0, 2)

    def test_v_prefix_and_equals(self):
        assert parse_version("v0.0.2") == (0, 0, 2)
        assert parse_version("=0.0.2") == (0, 0, 2)

    def test_prerelease_keeps_release_number(self):
        assert parse_version("0.0.2.dev1") == (0, 0, 2)

    def test_partial_drop(self):
        assert parse_version("0.1") == (0, 1)


class TestVersionCompare:
    def test_equal_with_v_prefix(self):
        assert compare_versions("0.0.2", "v0.0.2") == 0

    def test_newer(self):
        assert compare_versions("0.0.2", "v0.0.3") == -1
        assert compare_versions("0.0.3", "v0.0.2") == 1

    def test_minor_ordering(self):
        assert compare_versions("0.1.0", "0.0.9") == 1

    def test_dev_snapshot_never_up_to_date(self):
        assert compare_versions("0.0.2.dev1", "v0.0.2") == -1


class TestSelectAsset:
    def test_apt_amd64(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update.platform.machine", lambda: "x86_64")
        rel = _rel("v0.0.3", "comic-dl_0.0.3_amd64.deb", "other.txt")
        assert select_asset(rel, InstallKind.APT) == (
            "comic-dl_0.0.3_amd64.deb",
            "https://example.invalid/comic-dl_0.0.3_amd64.deb",
        )

    def test_rpm_x86_64(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update.platform.machine", lambda: "x86_64")
        rel = _rel("v0.0.3", "comic-dl-0.0.3-1.x86_64.rpm")
        assert select_asset(rel, InstallKind.RPM) == (
            "comic-dl-0.0.3-1.x86_64.rpm",
            "https://example.invalid/comic-dl-0.0.3-1.x86_64.rpm",
        )

    def test_pacman_x86_64(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update.platform.machine", lambda: "x86_64")
        rel = _rel("v0.0.3", "fallen020-comic-dl-0.0.3-1-x86_64.pkg.tar.zst")
        assert select_asset(rel, InstallKind.PACMAN) == (
            "fallen020-comic-dl-0.0.3-1-x86_64.pkg.tar.zst",
            "https://example.invalid/fallen020-comic-dl-0.0.3-1-x86_64.pkg.tar.zst",
        )

    def test_pacman_arm64_none(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update.platform.machine", lambda: "arm64")
        rel = _rel("v0.0.3", "fallen020-comic-dl-0.0.3-1-x86_64.pkg.tar.zst")
        assert select_asset(rel, InstallKind.PACMAN) is None

    def test_apt_ignores_other_arch(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update.platform.machine", lambda: "x86_64")
        rel = _rel("v0.0.3", "comic-dl_0.0.3_arm64.deb")
        assert select_asset(rel, InstallKind.APT) is None

    def test_unknown_kind(self):
        rel = _rel("v0.0.3", "comic-dl_0.0.3_amd64.deb")
        assert select_asset(rel, InstallKind.SOURCE) is None


class TestInstallArgs:
    def test_apt_command(self):
        argv = install_args(InstallKind.APT, Path("/x/comic.deb"))
        assert argv[0] == "apt"

    def test_pacman_command(self):
        argv = install_args(InstallKind.PACMAN, Path("/x/c.pkg.tar.zst"))
        assert argv[0] == "pacman" and "-U" in argv


class TestSelfVersion:
    async def test_self_version_prints(self, capsys):
        from comic_dl.cli import _run_self

        rc = await _run_self(["version"])
        assert rc == EXIT_OK
        assert f"comic-dl {_CORE_VERSION}" in _text(capsys)

    async def test_bare_self_is_usage_error(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "self"])
        from comic_dl.cli import main

        assert await main() == EXIT_USAGE

    async def test_deprecated_version_alias_still_works(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["prog", "--version"])
        from comic_dl.cli import main

        with pytest.raises(SystemExit) as exc:
            await main()
        assert exc.value.code == EXIT_OK
        assert f"comic-dl {_CORE_VERSION}" in _text(capsys)


class TestSelfErrors:
    """Friendly failure text for the self command group."""

    async def test_missing_command(self, capsys):
        from comic_dl.cli import _run_self

        rc = await _run_self([])
        assert rc == EXIT_USAGE
        out = _text(capsys)
        assert "missing command" in out
        assert "version" in out and "update" in out and "site" in out
        assert "comic-dl self --help" in out

    async def test_version_option_hint(self, capsys):
        from comic_dl.cli import _run_self

        rc = await _run_self(["--version"])
        assert rc == EXIT_USAGE
        assert "comic-dl self version" in _text(capsys)

    async def test_update_option_hint(self, capsys):
        from comic_dl.cli import _run_self

        rc = await _run_self(["--update"])
        assert rc == EXIT_USAGE
        assert "comic-dl self update" in _text(capsys)

    async def test_command_fuzzy_suggestion(self, capsys):
        from comic_dl.cli import _run_self

        rc = await _run_self(["updat"])
        assert rc == EXIT_USAGE
        out = _text(capsys)
        assert "Did you mean: update?" in out
        assert "Commands: version" not in out

    async def test_option_fuzzy_suggestion(self, capsys):
        from comic_dl.cli import _run_self

        rc = await _run_self(["--hep"])
        assert rc == EXIT_USAGE
        assert "Did you mean: --help?" in _text(capsys)

    async def test_unknown_without_suggestion(self, capsys):
        from comic_dl.cli import _run_self

        rc = await _run_self(["deploy"])
        assert rc == EXIT_USAGE
        assert "unknown command 'deploy'" in _text(capsys)

    async def test_hints_come_from_parser(self, monkeypatch, capsys):
        import argparse

        from comic_dl.cli import _build_self_parser, _run_self, _self_hint_terms

        commands, flags = _self_hint_terms()
        sub = next(
            a for a in _build_self_parser()._actions if isinstance(a, argparse._SubParsersAction)
        )
        assert commands == list(sub.choices)
        assert {"--check", "-y", "--yes"} <= set(flags)
        monkeypatch.setattr("comic_dl.cli._self_hint_terms", lambda: (["zzz"], flags))
        assert await _run_self(["updat"]) == EXIT_USAGE
        assert "Did you mean" not in _text(capsys)

    async def test_help_option_shows_group_help(self, capsys):
        from comic_dl.cli import _run_self

        assert await _run_self(["--help"]) == EXIT_OK
        assert "comic-dl self <COMMAND>" in _text(capsys)


class TestUpdateCheck:
    async def test_network_failure(self, monkeypatch, capsys):
        async def boom():
            return ReleaseFailure(ReleaseFailureKind.OFFLINE)

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", boom)
        rc = await run_update_command(check=True, yes=True)
        assert rc == EXIT_ERROR
        out = _text(capsys)
        assert "Unable to check for updates: no network connection." in out
        assert "--proxy" in out

    async def test_already_up_to_date(self, monkeypatch, capsys):
        async def current():
            return _rel("v0.0.2")

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", current)
        rc = await run_update_command(check=True, yes=True)
        assert rc == EXIT_OK
        out = _text(capsys)
        assert "already up to date" in out
        assert "Installed via:" in out

    async def test_update_available_check_only(self, monkeypatch, capsys):
        async def newer():
            return _rel(_FUTURE_TAG)

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.PIP, _MOCK_EXE, writable=True),
        )
        rc = await run_update_command(check=True, yes=True)
        assert rc == EXIT_OK
        out = _text(capsys)
        assert "update available" in out
        assert "Installed version:" in out and "Latest version:" in out
        assert "releases/tag/" in out and _FUTURE_TAG in out

    async def test_quiet_check_offline(self, monkeypatch, capsys):
        async def boom():
            return ReleaseFailure(ReleaseFailureKind.OFFLINE)

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", boom)
        rc = await run_update_command(check=True, yes=True, quiet=True)
        assert rc == EXIT_ERROR
        assert "Unable to check for updates" in _text(capsys)

    async def test_quiet_flag_threaded_from_cli(self, monkeypatch, capsys):
        from comic_dl.cli import _run_self

        async def boom():
            return ReleaseFailure(ReleaseFailureKind.OFFLINE)

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", boom)
        rc = await _run_self(["update", "--check", "--quiet"])
        assert rc == EXIT_ERROR
        assert "Unable to check for updates" in _text(capsys)


class TestReleaseLookup:
    """`fetch_latest_release` classifies failures instead of collapsing them."""

    class _Resp:
        """Minimal stand-in for the response the lookup reads."""

        def __init__(self, status, headers=None, payload=None, broken=False):
            self.status_code = status
            self.headers = headers or {}
            self._payload = payload
            self._broken = broken

        def json(self):
            if self._broken:
                raise ValueError("not JSON")
            return self._payload

    async def test_transport_error_is_offline(self, monkeypatch):
        async def dead(url, client, **kw):
            raise OSError("down")

        monkeypatch.setattr("comic_dl.self_update.BaseScraper._timeout_get", dead)
        assert await fetch_latest_release() == ReleaseFailure(ReleaseFailureKind.OFFLINE)

    async def test_rate_limit_names_reset(self, monkeypatch):
        async def limited(url, client, **kw):
            return TestReleaseLookup._Resp(429, {"x-ratelimit-reset": "50400"})

        monkeypatch.setattr("comic_dl.self_update.BaseScraper._timeout_get", limited)
        result = await fetch_latest_release()
        assert result.kind is ReleaseFailureKind.RATE_LIMITED
        assert result.detail == "14:00 UTC"

    async def test_exhausted_quota_is_rate_limited(self, monkeypatch):
        async def limited(url, client, **kw):
            return TestReleaseLookup._Resp(
                403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "50400"}
            )

        monkeypatch.setattr("comic_dl.self_update.BaseScraper._timeout_get", limited)
        result = await fetch_latest_release()
        assert result.kind is ReleaseFailureKind.RATE_LIMITED
        assert result.detail == "14:00 UTC"

    async def test_retry_after_counts_from_now(self, monkeypatch):
        monkeypatch.setattr(time, "time", lambda: 1_000_000.0)

        async def limited(url, client, **kw):
            return TestReleaseLookup._Resp(429, {"retry-after": "3600"})

        monkeypatch.setattr("comic_dl.self_update.BaseScraper._timeout_get", limited)
        result = await fetch_latest_release()
        assert result.kind is ReleaseFailureKind.RATE_LIMITED
        assert result.detail == "14:46 UTC"

    async def test_not_found(self, monkeypatch):
        async def gone(url, client, **kw):
            return TestReleaseLookup._Resp(404)

        monkeypatch.setattr("comic_dl.self_update.BaseScraper._timeout_get", gone)
        assert await fetch_latest_release() == ReleaseFailure(ReleaseFailureKind.NOT_FOUND)

    async def test_server_error_carries_status(self, monkeypatch):
        async def broken(url, client, **kw):
            return TestReleaseLookup._Resp(500)

        monkeypatch.setattr("comic_dl.self_update.BaseScraper._timeout_get", broken)
        result = await fetch_latest_release()
        assert result.kind is ReleaseFailureKind.SERVER_ERROR
        assert result.detail == "500"

    async def test_unexhausted_403_is_server_error(self, monkeypatch):
        async def denied(url, client, **kw):
            return TestReleaseLookup._Resp(403, {"x-ratelimit-remaining": "59"})

        monkeypatch.setattr("comic_dl.self_update.BaseScraper._timeout_get", denied)
        result = await fetch_latest_release()
        assert result.kind is ReleaseFailureKind.SERVER_ERROR
        assert result.detail == "403"

    async def test_malformed_payloads(self, monkeypatch):
        async def broken(url, client, **kw):
            return TestReleaseLookup._Resp(200, broken=True)

        async def tagless(url, client, **kw):
            return TestReleaseLookup._Resp(200, payload={})

        monkeypatch.setattr("comic_dl.self_update.BaseScraper._timeout_get", broken)
        assert await fetch_latest_release() == ReleaseFailure(ReleaseFailureKind.MALFORMED)
        monkeypatch.setattr("comic_dl.self_update.BaseScraper._timeout_get", tagless)
        assert await fetch_latest_release() == ReleaseFailure(ReleaseFailureKind.MALFORMED)

    async def test_non_object_payload_is_malformed(self, monkeypatch):
        async def listed(url, client, **kw):
            return TestReleaseLookup._Resp(200, payload=["v1"])

        monkeypatch.setattr("comic_dl.self_update.BaseScraper._timeout_get", listed)
        assert await fetch_latest_release() == ReleaseFailure(ReleaseFailureKind.MALFORMED)

    async def test_report_names_each_cause(self, capsys):
        report_release_failure(ReleaseFailure(ReleaseFailureKind.NOT_FOUND))
        report_release_failure(ReleaseFailure(ReleaseFailureKind.MALFORMED))
        report_release_failure(ReleaseFailure(ReleaseFailureKind.SERVER_ERROR, detail="500"))
        report_release_failure(ReleaseFailure(ReleaseFailureKind.RATE_LIMITED, detail="14:00 UTC"))
        out = _text(capsys)
        assert "api.github.com" in out
        assert "unexpected response" in out
        assert "HTTP 500" in out
        assert "resets at 14:00 UTC" in out


class TestUpdateRefusal:
    async def test_source_checkout(self, monkeypatch, capsys):
        async def newer():
            return _rel(_FUTURE_TAG)

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.SOURCE, _MOCK_EXE),
        )
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_OK
        out = _text(capsys)
        assert "git pull" in out and "uv sync" in out

    async def test_unknown_installation(self, monkeypatch):
        async def newer():
            return _rel(_FUTURE_TAG)

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.UNKNOWN, _MOCK_EXE),
        )
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_ERROR

    async def test_binary_reports_only(self, monkeypatch, capsys):
        async def newer():
            return _rel(_FUTURE_TAG)

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.BINARY, _MOCK_EXE),
        )
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_OK
        assert "github.com/fallen020/comic-dl" in _text(capsys)


class TestPipUpdate:
    async def test_not_writable(self, monkeypatch, capsys):
        async def newer():
            return _rel(_FUTURE_TAG)

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.PIP, _MOCK_EXE, writable=False),
        )
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_ERROR
        assert "not writable" in _text(capsys)

    async def test_yes_runs_pip(self, monkeypatch):
        import sys as _sys

        async def newer():
            return _rel(_FUTURE_TAG)

        calls: list[list[str]] = []

        def fake_subprocess(argv, **_kw):
            calls.append(list(argv))
            return type("P", (), {"returncode": 0})()

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.PIP, _MOCK_EXE, writable=True),
        )
        monkeypatch.setattr("comic_dl.self_update.subprocess.run", fake_subprocess)
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_OK
        assert calls and calls[0][:4] == [_sys.executable, "-m", "pip", "install"]

    async def test_pip_failure(self, monkeypatch, capsys):
        async def newer():
            return _rel(_FUTURE_TAG)

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.PIP, _MOCK_EXE, writable=True),
        )
        monkeypatch.setattr(
            "comic_dl.self_update.subprocess.run",
            lambda *a, **k: type("P", (), {"returncode": 1})(),
        )
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_ERROR
        assert "pip upgrade failed" in _text(capsys)


class TestUvUpdate:
    async def test_uv_command(self, monkeypatch):
        async def newer():
            return _rel(_FUTURE_TAG)

        calls: list[list[str]] = []

        def fake_subprocess(argv, **_kw):
            calls.append(list(argv))
            return type("P", (), {"returncode": 0})()

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.UV, _MOCK_EXE, writable=True),
        )
        monkeypatch.setattr("comic_dl.self_update.subprocess.run", fake_subprocess)
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_OK
        assert calls and calls[0] == ["uv", "tool", "upgrade", "comic-dl"]


class TestPackageManagerUpdate:
    async def test_sudo_apt_install(self, monkeypatch, capsys):
        async def newer():
            return _rel(_FUTURE_TAG, f"comic-dl_{_FUTURE}_amd64.deb")

        async def fake_download(url, dest, **_ignored):
            dest.write_bytes(b"pkg")

        calls: list[list[str]] = []

        def fake_subprocess(argv, **_kw):
            calls.append(list(argv))
            return type("P", (), {"returncode": 0})()

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.APT, _MOCK_EXE),
        )
        monkeypatch.setattr("comic_dl.self_update.platform.machine", lambda: "amd64")
        monkeypatch.setattr("comic_dl.self_update.download_artifact", fake_download)
        monkeypatch.setattr(
            "comic_dl.self_update._fetch_checksums",
            lambda release: _checksums_for(
                {f"comic-dl_{_FUTURE}_amd64.deb": hashlib.sha256(b"pkg").hexdigest()}
            ),
        )
        monkeypatch.setattr("comic_dl.self_update._request_confirmation", lambda *a, **k: None)
        monkeypatch.setattr("comic_dl.self_update._is_root", lambda: False)
        monkeypatch.setattr(
            "comic_dl.self_update.shutil.which",
            lambda name: "/usr/bin/sudo" if name == "sudo" else None,
        )
        monkeypatch.setattr("comic_dl.self_update.subprocess.run", fake_subprocess)
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_OK
        assert len(calls) == 1
        assert calls[0][0] == "/usr/bin/sudo" and calls[0][1] == "apt"
        assert "Checksum verified (sha256)" in _text(capsys)

    async def test_sudo_absent_instructs(self, monkeypatch):
        async def newer():
            return _rel(_FUTURE_TAG, f"comic-dl_{_FUTURE}_amd64.deb")

        async def fake_download(url, dest, **_ignored):
            dest.write_bytes(b"pkg")

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.APT, _MOCK_EXE),
        )
        monkeypatch.setattr("comic_dl.self_update.platform.machine", lambda: "amd64")
        monkeypatch.setattr("comic_dl.self_update.download_artifact", fake_download)
        monkeypatch.setattr(
            "comic_dl.self_update._fetch_checksums",
            lambda release: _checksums_for(
                {f"comic-dl_{_FUTURE}_amd64.deb": hashlib.sha256(b"pkg").hexdigest()}
            ),
        )
        monkeypatch.setattr("comic_dl.self_update._request_confirmation", lambda *a, **k: None)
        monkeypatch.setattr("comic_dl.self_update._is_root", lambda: False)
        monkeypatch.setattr("comic_dl.self_update.shutil.which", lambda name: None)
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_ERROR

    async def test_package_install_failure_instructs(self, monkeypatch):
        async def newer():
            return _rel(_FUTURE_TAG, f"comic-dl_{_FUTURE}_amd64.deb")

        async def fake_download(url, dest, **_ignored):
            dest.write_bytes(b"pkg")

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.APT, _MOCK_EXE),
        )
        monkeypatch.setattr("comic_dl.self_update.platform.machine", lambda: "amd64")
        monkeypatch.setattr("comic_dl.self_update.download_artifact", fake_download)
        monkeypatch.setattr(
            "comic_dl.self_update._fetch_checksums",
            lambda release: _checksums_for(
                {f"comic-dl_{_FUTURE}_amd64.deb": hashlib.sha256(b"pkg").hexdigest()}
            ),
        )
        monkeypatch.setattr("comic_dl.self_update._request_confirmation", lambda *a, **k: None)
        monkeypatch.setattr("comic_dl.self_update._is_root", lambda: False)
        monkeypatch.setattr(
            "comic_dl.self_update.shutil.which",
            lambda name: "/usr/bin/sudo" if name == "sudo" else None,
        )
        monkeypatch.setattr(
            "comic_dl.self_update.subprocess.run",
            lambda argv, **_kw: type("P", (), {"returncode": 100})(),
        )
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_ERROR


class TestDownloadProgress:
    async def test_reports_cumulative_bytes(self, monkeypatch, tmp_path):
        seen: list[tuple[int, int | None]] = []

        class FakeResp:
            status_code = 200
            headers = {"content-length": "10"}

            async def aiter_content(self, _size):
                yield b"12345"
                yield b"67890"

            async def aclose(self):
                pass

        async def fake_stream(_client, _url):
            return FakeResp()

        monkeypatch.setattr("comic_dl.self_update._open_stream", fake_stream)
        dest = tmp_path / "pkg.deb"
        await download_artifact(
            "https://example.invalid/pkg.deb", dest, on_progress=lambda d, t: seen.append((d, t))
        )
        assert dest.read_bytes() == b"1234567890"
        assert seen == [(5, 10), (10, 10)]

    async def test_missing_length_is_indeterminate(self, monkeypatch, tmp_path):
        seen: list[tuple[int, int | None]] = []

        class FakeResp:
            status_code = 200
            headers = {}

            async def aiter_content(self, _size):
                yield b"12345"

            async def aclose(self):
                pass

        async def fake_stream(_client, _url):
            return FakeResp()

        monkeypatch.setattr("comic_dl.self_update._open_stream", fake_stream)
        await download_artifact(
            "https://example.invalid/pkg.deb",
            tmp_path / "pkg.deb",
            on_progress=lambda d, t: seen.append((d, t)),
        )
        assert seen == [(5, None)]


class TestChecksumVerification:
    async def test_mismatch_refuses_before_install(self, monkeypatch, capsys):
        async def newer():
            return _rel(_FUTURE_TAG, f"comic-dl_{_FUTURE}_amd64.deb")

        async def fake_download(url, dest, **_ignored):
            dest.write_bytes(b"pkg")

        calls: list[list[str]] = []

        def fake_subprocess(argv, **_kw):
            calls.append(list(argv))
            return type("P", (), {"returncode": 0})()

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.APT, _MOCK_EXE),
        )
        monkeypatch.setattr("comic_dl.self_update.platform.machine", lambda: "amd64")
        monkeypatch.setattr("comic_dl.self_update.download_artifact", fake_download)
        monkeypatch.setattr(
            "comic_dl.self_update._fetch_checksums",
            lambda release: _checksums_for({f"comic-dl_{_FUTURE}_amd64.deb": "0" * 64}),
        )
        monkeypatch.setattr("comic_dl.self_update._request_confirmation", lambda *a, **k: None)
        monkeypatch.setattr("comic_dl.self_update.subprocess.run", fake_subprocess)
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_ERROR
        assert calls == []
        assert "mismatch" in _text(capsys)

    async def test_missing_checksums_refuses_before_install(self, monkeypatch, capsys):
        async def newer():
            return _rel(_FUTURE_TAG, f"comic-dl_{_FUTURE}_amd64.deb")

        async def fake_download(url, dest, **_ignored):
            dest.write_bytes(b"pkg")

        calls: list[list[str]] = []

        def fake_subprocess(argv, **_kw):
            calls.append(list(argv))
            return type("P", (), {"returncode": 0})()

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.APT, _MOCK_EXE),
        )
        monkeypatch.setattr("comic_dl.self_update.platform.machine", lambda: "amd64")
        monkeypatch.setattr("comic_dl.self_update.download_artifact", fake_download)
        monkeypatch.setattr(
            "comic_dl.self_update._fetch_checksums", lambda release: _checksums_for({})
        )
        monkeypatch.setattr("comic_dl.self_update._request_confirmation", lambda *a, **k: None)
        monkeypatch.setattr("comic_dl.self_update.subprocess.run", fake_subprocess)
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_ERROR
        assert calls == []
        assert "No checksum published" in _text(capsys)

    def test_parse_checksums_skips_malformed(self):
        from comic_dl.self_update import _parse_checksums

        parsed = _parse_checksums(
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  empty.deb\n"
            "5d41402abc4b2a76b9719d911017c5925d41402abc4b2a76b9719d911017c592 *star.deb\n"
            "short line\n"
            "notahex  file1.deb\n"
        )
        assert parsed == {
            "empty.deb": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "star.deb": "5d41402abc4b2a76b9719d911017c5925d41402abc4b2a76b9719d911017c592",
        }

    def test_verify_artifact_round_trip(self, tmp_path):
        from comic_dl.self_update import _verify_artifact

        target = tmp_path / "pkg.deb"
        target.write_bytes(b"pkg")
        good = hashlib.sha256(b"pkg").hexdigest()
        assert _verify_artifact({"pkg.deb": good}, "pkg.deb", target) is True
        assert _verify_artifact({"pkg.deb": "0" * 64}, "pkg.deb", target) is False
        assert _verify_artifact({}, "pkg.deb", target) is False

    async def test_no_asset_for_arch(self, monkeypatch, capsys):
        async def newer():
            return _rel(_FUTURE_TAG, f"comic-dl_{_FUTURE}_amd64.deb")

        monkeypatch.setattr("comic_dl.self_update.fetch_latest_release", newer)
        monkeypatch.setattr(
            "comic_dl.self_update.detect_installation",
            lambda: InstallationInfo(InstallKind.PACMAN, _MOCK_EXE),
        )
        monkeypatch.setattr("comic_dl.self_update.platform.machine", lambda: "arm64")
        rc = await run_update_command(check=False, yes=True)
        assert rc == EXIT_ERROR
        assert "No pacman package" in _text(capsys)


class TestConfirmation:
    def test_yes_skips(self):
        assert _request_confirmation(True, "?", says_interactive_no="skipped") is None

    def test_noninteractive_refuses(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update._is_interactive", lambda: False)
        assert _request_confirmation(False, "?", says_interactive_no="skipped") == EXIT_INTERRUPTED

    def test_interactive_no_skips(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update._is_interactive", lambda: True)
        monkeypatch.setattr("comic_dl.self_update.Confirm.ask", lambda *a, **k: False)
        assert _request_confirmation(False, "?", says_interactive_no="skipped") == EXIT_OK

    def test_interactive_yes(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update._is_interactive", lambda: True)
        monkeypatch.setattr("comic_dl.self_update.Confirm.ask", lambda *a, **k: True)
        assert _request_confirmation(False, "?", says_interactive_no="skipped") is None


class TestDetection:
    def test_frozen_is_binary(self, monkeypatch, tmp_path):
        monkeypatch.setattr("comic_dl.self_update.sys.frozen", True, raising=False)
        info = detect_installation()
        assert info.kind is InstallKind.BINARY

    def test_os_owner_authoritative(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update.sys.frozen", False, raising=False)
        monkeypatch.setattr(
            "comic_dl.self_update._os_package_owner", lambda script: InstallKind.PACMAN
        )
        info = detect_installation()
        assert info.kind is InstallKind.PACMAN

    def test_unknown_without_distribution(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update.sys.frozen", False, raising=False)
        monkeypatch.setattr("comic_dl.self_update._distribution", lambda: None)
        info = detect_installation()
        assert info.kind is InstallKind.UNKNOWN

    def test_editable_is_source(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update.sys.frozen", False, raising=False)
        monkeypatch.setattr("comic_dl.self_update._distribution", lambda: _FakeDist(editable=True))
        monkeypatch.setattr("comic_dl.self_update._os_package_owner", lambda s: None)
        info = detect_installation()
        assert info.kind is InstallKind.SOURCE

    def test_venv_is_pip(self, monkeypatch):
        monkeypatch.setattr("comic_dl.self_update.sys.frozen", False, raising=False)
        monkeypatch.setattr("comic_dl.self_update.sys.prefix", "/venv/prefix")
        monkeypatch.setattr("comic_dl.self_update.sys.base_prefix", "/usr")
        monkeypatch.setattr("comic_dl.self_update._distribution", lambda: _FakeDist(editable=False))
        monkeypatch.setattr("comic_dl.self_update._os_package_owner", lambda s: None)
        monkeypatch.setattr("comic_dl.self_update._writable", lambda p: True)
        info = detect_installation()
        assert info.kind is InstallKind.PIP
        assert info.writable is True

    def test_uv_tool(self, monkeypatch, tmp_path):
        tools = tmp_path / "tools"
        exe = tools / "comic-dl" / ".venv" / "bin" / "python"
        monkeypatch.setattr("comic_dl.self_update.sys.frozen", False, raising=False)
        monkeypatch.setattr("comic_dl.self_update.sys.executable", str(exe))
        monkeypatch.setattr("comic_dl.self_update.sys.prefix", "/x")
        monkeypatch.setattr("comic_dl.self_update.sys.base_prefix", "/usr")
        monkeypatch.setattr("comic_dl.self_update._distribution", lambda: _FakeDist(editable=False))
        monkeypatch.setattr("comic_dl.self_update._os_package_owner", lambda s: None)
        monkeypatch.setattr("os.environ", {"UV_TOOL_DIR": str(tools)})
        info = detect_installation()
        assert info.kind is InstallKind.UV


class _FakeDist:
    """Minimal stand-in for ``importlib.metadata.Distribution``."""

    def __init__(self, editable: bool):
        self._editable = editable

    def locate(self):
        return Path("/site/comic_dl")

    def read_text(self, filename: str):
        if filename == "direct_url.json":
            return '{"dir_info": {"editable": true}}' if self._editable else '{"dir_info": {}}'
        raise FileNotFoundError(filename)
