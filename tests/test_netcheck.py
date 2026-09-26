from __future__ import annotations

import asyncio

import pytest

from comic_dl import netcheck


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts from an unprobed process."""
    netcheck.reset_connectivity_cache()
    yield
    netcheck.reset_connectivity_cache()


def _stub(monkeypatch, *, tcp, http):
    monkeypatch.setattr(netcheck, "_tcp_reachable", _fake(tcp))
    monkeypatch.setattr(netcheck, "_http_reachable", _fake(http))


def _fake(results):
    async def _inner(*args, **kwargs):
        return results

    return _inner


class TestVerdict:
    @pytest.mark.asyncio
    async def test_tcp_success_is_online(self, monkeypatch):
        _stub(monkeypatch, tcp=True, http=False)
        assert await netcheck.check_connectivity() is True

    @pytest.mark.asyncio
    async def test_http_success_alone_is_online(self, monkeypatch):
        """Socket blocked by a firewall must not veto a working HTTP path."""
        _stub(monkeypatch, tcp=False, http=True)
        assert await netcheck.check_connectivity() is True

    @pytest.mark.asyncio
    async def test_both_failing_is_offline(self, monkeypatch):
        _stub(monkeypatch, tcp=False, http=False)
        assert await netcheck.check_connectivity() is False

    @pytest.mark.asyncio
    async def test_probe_itself_broken_reports_online(self, monkeypatch):
        """A diagnostic must never be the reason a run is refused."""

        async def _boom(*args, **kwargs):
            raise RuntimeError("probe exploded")

        monkeypatch.setattr(netcheck, "_probe", _boom)
        assert await netcheck.check_connectivity() is True


class TestCaching:
    @pytest.mark.asyncio
    async def test_result_is_cached_between_calls(self, monkeypatch):
        calls = {"n": 0}

        async def _counting(*args, **kwargs):
            calls["n"] += 1
            return True

        monkeypatch.setattr(netcheck, "_probe", _counting)
        assert await netcheck.check_connectivity() is True
        assert await netcheck.check_connectivity() is True
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_force_reprobes(self, monkeypatch):
        calls = {"n": 0}

        async def _counting(*args, **kwargs):
            calls["n"] += 1
            return False

        monkeypatch.setattr(netcheck, "_probe", _counting)
        assert await netcheck.check_connectivity() is False
        assert await netcheck.check_connectivity(force=True) is False
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_reset_clears_the_cached_verdict(self, monkeypatch):
        calls = {"n": 0}

        async def _counting(*args, **kwargs):
            calls["n"] += 1
            return True

        monkeypatch.setattr(netcheck, "_probe", _counting)
        assert await netcheck.check_connectivity() is True
        netcheck.reset_connectivity_cache()
        assert await netcheck.check_connectivity() is True
        assert calls["n"] == 2


class TestProbe:
    @pytest.mark.asyncio
    async def test_first_success_ends_the_check(self, monkeypatch):
        """A target that never answers must not tax an online run."""
        state = {"cancelled": False}

        async def _fast(*args, **kwargs):
            return True

        async def _hang(*args, **kwargs):
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise
            return False

        monkeypatch.setattr(netcheck, "_tcp_reachable", _fast)
        monkeypatch.setattr(netcheck, "_http_reachable", _hang)

        loop = asyncio.get_running_loop()
        started = loop.time()
        assert await netcheck._probe() is True
        assert loop.time() - started < 1.0
        assert state["cancelled"] is True

    @pytest.mark.asyncio
    async def test_all_failing_probes_report_offline(self, monkeypatch):
        _stub(monkeypatch, tcp=False, http=False)
        assert await netcheck._probe() is False


class TestProbes:
    @pytest.mark.asyncio
    async def test_tcp_probe_closes_the_socket(self, monkeypatch):
        class _Writer:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

            async def wait_closed(self):
                return None

        writer = _Writer()

        async def _open(host, port, **kwargs):
            return (None, writer)

        monkeypatch.setattr(netcheck.asyncio, "open_connection", _open)
        assert await netcheck._tcp_reachable("1.1.1.1", 443) is True
        assert writer.closed is True

    @pytest.mark.asyncio
    async def test_tcp_probe_swallows_oserror(self, monkeypatch):
        async def _refuse(host, port, **kwargs):
            raise OSError("refused")

        monkeypatch.setattr(netcheck.asyncio, "open_connection", _refuse)
        assert await netcheck._tcp_reachable("1.1.1.1", 443) is False

    @pytest.mark.asyncio
    async def test_http_probe_counts_a_4xx_as_reachable(self, monkeypatch):
        """A 404 still proves the network carried a round trip."""

        class _Resp:
            status_code = 404

        class _Session:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, url, **kwargs):
                return _Resp()

        monkeypatch.setattr(netcheck, "AsyncSession", _Session)

        async def _ok(url):
            return url

        monkeypatch.setattr(netcheck, "validate_request_url_async", _ok)
        assert await netcheck._http_reachable("https://pypi.org/simple/") is True

    @pytest.mark.asyncio
    async def test_http_probe_respects_url_validation(self, monkeypatch):
        """The probe must not become a hole in the SSRF guard."""

        async def _blocked(url):
            raise RuntimeError("blocked")

        monkeypatch.setattr(netcheck, "validate_request_url_async", _blocked)
        assert await netcheck._http_reachable("http://127.0.0.1/") is False


def _stub_probe(monkeypatch, online: bool):
    """Answer the CLI's pre-flight without probing anything."""
    import argparse
    from pathlib import Path

    async def _check(*, force: bool = False) -> bool:
        return online

    monkeypatch.setattr("comic_dl.cli.check_connectivity", _check)

    async def _never_called(url, **kwargs):
        raise AssertionError("pre-flight must abort before any request")

    monkeypatch.setattr("comic_dl.cli.process_url", _never_called)
    monkeypatch.setattr("comic_dl.cli._build_downloaded_index", lambda out: {})
    monkeypatch.setattr("sys.argv", ["prog", "-u", "https://a.com/x", "--json"])
    monkeypatch.setattr(
        "comic_dl.cli.parse_urls",
        lambda: (
            ["https://a.com/x"],
            argparse.Namespace(
                quiet=True,
                output=Path("/tmp"),
                concurrency=5,
                parallel=5,
                force=False,
                max_image_size=100 * 1024 * 1024,
                max_size=0,
                chapter_parallel=1,
                chapters=None,
                json=True,
                url="https://a.com/x",
                dry_run=False,
            ),
        ),
    )


class TestCliPreflight:
    @pytest.mark.asyncio
    async def test_offline_aborts_before_any_request(self, monkeypatch, capsys):
        _stub_probe(monkeypatch, online=False)

        from comic_dl.cli import main

        assert await main() == 1
        err = capsys.readouterr().err.replace("\n", "")
        assert "No internet connection" in err
        # One clear failure beats the same message repeated per URL.
        assert "https://a.com/x" not in err

    @pytest.mark.asyncio
    async def test_online_continues_to_the_scrape(self, monkeypatch, capsys):
        _stub_probe(monkeypatch, online=True)
        seen = []

        async def _process(url, **kwargs):
            seen.append(url)
            return "failed", "x"

        monkeypatch.setattr("comic_dl.cli.process_url", _process)

        from comic_dl.cli import main

        assert await main() == 1
        assert seen == ["https://a.com/x"]


class TestCliErrorRefinement:
    @pytest.mark.asyncio
    async def test_network_error_names_the_host_when_online(self, monkeypatch, capsys):
        """The internet is up, so blaming the user's connection is wrong."""
        _stub_probe(monkeypatch, online=True)

        from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError

        async def _process(url, **kwargs):
            raise CurlConnectionError("could not resolve host")

        monkeypatch.setattr("comic_dl.cli.process_url", _process)

        from comic_dl.cli import main

        assert await main() == 1
        out = capsys.readouterr()
        assert "Could not reach a.com" in out.out.replace("\n", "") + out.err.replace("\n", "")
        assert "Check your internet connection" not in out.out.replace("\n", "")

    @pytest.mark.asyncio
    async def test_offline_keeps_the_connection_message(self, monkeypatch, capsys):
        _stub_probe(monkeypatch, online=False)

        from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError

        async def _process(url, **kwargs):
            raise CurlConnectionError("network unreachable")

        monkeypatch.setattr("comic_dl.cli.process_url", _process)

        from comic_dl.cli import main

        assert await main() == 1
        err = capsys.readouterr().err.replace("\n", "")
        assert "No internet connection" in err
