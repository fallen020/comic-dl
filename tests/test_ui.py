from __future__ import annotations

import ast
import asyncio
import io
import re
import time
from pathlib import Path

import pytest
from rich.color import ColorSystem
from rich.console import Console

import comic_dl.ui as ui_module
from comic_dl.ui import (
    DIAGNOSTIC,
    ETA,
    NORMAL,
    TAG_HTTP,
    TRACE,
    VERBOSE,
    Activity,
    Pipeline,
    SourceRow,
    _checkbox_renderable,
    _decode_key,
    _format_remaining,
    _http_trace_enabled,
    checkbox_prompt,
    http_event,
    make_download_progress,
    print_batch_summary,
    print_chapter_preview,
    print_dim,
    print_error,
    print_failure_recap,
    print_header,
    print_meta,
    print_skipped,
    print_success,
    print_summary,
    print_warning,
    redact_url,
    render_sources_table,
    set_debug_file,
    set_verbosity,
    source_search,
    stage_line,
    trace,
    vlog,
)


class TestVerbosity:
    @pytest.fixture(autouse=True)
    def _reset(self):
        set_verbosity(0)
        yield
        set_verbosity(0)

    def test_set_verbosity_clamps_high(self):
        set_verbosity(5)
        assert ui_module.VERBOSITY == TRACE

    def test_set_verbosity_clamps_low(self):
        set_verbosity(-1)
        assert ui_module.VERBOSITY == NORMAL

    def test_set_verbosity_exact(self):
        set_verbosity(2)
        assert ui_module.VERBOSITY == 2

    def test_vlog_hidden_below_level(self, capsys):
        set_verbosity(1)
        vlog(DIAGNOSTIC, "secret detail")
        vlog(VERBOSE, "visible")
        err = capsys.readouterr().err
        assert "secret detail" not in err
        assert "visible" in err

    def test_vlog_renders_tag(self, capsys):
        set_verbosity(DIAGNOSTIC)
        vlog(DIAGNOSTIC, "GET 200 https://x", tag=TAG_HTTP)
        err = capsys.readouterr().err
        assert "[http] GET 200 https://x" in err

    def test_vlog_goes_to_stderr(self, capsys):
        set_verbosity(TRACE)
        vlog(TRACE, "trace line")
        captured = capsys.readouterr()
        assert "trace line" in captured.err
        assert "trace line" not in captured.out


class TestHttpEvent:
    @pytest.fixture(autouse=True)
    def _reset(self):
        set_verbosity(0)
        yield
        set_verbosity(0)

    def test_renders_request_and_timing(self, capsys, monkeypatch):
        monkeypatch.delenv("COMIC_DL_TRACE_HTTP", raising=False)
        set_verbosity(DIAGNOSTIC)
        http_event("GET", "https://x/1", status=200, duration=0.125)
        err = capsys.readouterr().err
        assert "[http] GET 200 https://x/1" in err
        assert "125 ms" in err

    def test_hidden_below_level(self, capsys):
        set_verbosity(VERBOSE)
        http_event("GET", "https://x/1", status=200)
        assert "https://x/1" not in capsys.readouterr().err

    def test_error_path(self, capsys, monkeypatch):
        monkeypatch.delenv("COMIC_DL_TRACE_HTTP", raising=False)
        set_verbosity(DIAGNOSTIC)
        http_event("GET", "https://x/1", error="connection refused")
        err = capsys.readouterr().err
        assert "connection refused" in err
        assert "https://x/1" in err

    def test_headers_only_at_trace_without_env(self, capsys, monkeypatch):
        monkeypatch.delenv("COMIC_DL_TRACE_HTTP", raising=False)
        set_verbosity(DIAGNOSTIC)
        http_event("GET", "https://x/1", status=200, headers={"content-type": "image/webp"})
        assert "content-type" not in capsys.readouterr().err
        set_verbosity(TRACE)
        http_event("GET", "https://x/2", status=200, headers={"content-type": "image/webp"})
        err = capsys.readouterr().err
        assert "content-type: image/webp" in err

    def test_env_enables_headers_without_trace(self, capsys, monkeypatch):
        monkeypatch.setenv("COMIC_DL_TRACE_HTTP", "1")
        set_verbosity(DIAGNOSTIC)
        http_event("GET", "https://x/1", status=200, headers={"content-type": "image/webp"})
        err = capsys.readouterr().err
        assert "content-type: image/webp" in err

    def test_noise_headers_filtered_in_trace(self, capsys, monkeypatch):
        monkeypatch.setenv("COMIC_DL_TRACE_HTTP", "1")
        set_verbosity(TRACE)
        http_event(
            "GET",
            "https://x/1",
            status=200,
            headers={
                "server": "cloudflare",
                "alt-svc": "h3=:443",
                "speculation-rules": "/cdn-cgi/speculation",
                "report-to": '{"group":"cf-nel"}',
                "content-type": "text/html",
            },
        )
        err = capsys.readouterr().err
        assert "content-type: text/html" in err
        assert "server" not in err
        assert "alt-svc" not in err
        assert "speculation-rules" not in err
        assert "report-to" not in err

    def test_env_widest_depth_only(self, monkeypatch):
        monkeypatch.delenv("COMIC_DL_TRACE_HTTP", raising=False)
        assert _http_trace_enabled() is False
        monkeypatch.setenv("COMIC_DL_TRACE_HTTP", "0")
        assert _http_trace_enabled() is False
        monkeypatch.setenv("COMIC_DL_TRACE_HTTP", "headers")
        assert _http_trace_enabled() is True


class TestHttpEventRedaction:
    @pytest.fixture(autouse=True)
    def _reset(self):
        set_verbosity(0)
        yield
        set_verbosity(0)

    def test_sensitive_header_masked_in_trace(self, capsys, monkeypatch):
        monkeypatch.setenv("COMIC_DL_TRACE_HTTP", "1")
        set_verbosity(DIAGNOSTIC)
        http_event(
            "GET",
            "https://s/1",
            status=200,
            headers={"set-cookie": "secret=abc", "etag": '"abc"'},
        )
        err = capsys.readouterr().err
        assert 'etag: "abc"' in err
        assert "secret=abc" not in err
        assert "set-cookie" not in err

    def test_auth_header_masked(self, capsys, monkeypatch):
        monkeypatch.setenv("COMIC_DL_TRACE_HTTP", "1")
        set_verbosity(DIAGNOSTIC)
        http_event(
            "GET", "https://s/1", status=200, headers={"authorization": "Bearer abcdef"}
        )
        err = capsys.readouterr().err
        assert "Bearer abcdef" not in err
        assert "authorization" not in err

    def test_very_long_header_value_truncated(self, capsys, monkeypatch):
        monkeypatch.setenv("COMIC_DL_TRACE_HTTP", "1")
        set_verbosity(DIAGNOSTIC)
        http_event(
            "GET",
            "https://s/1",
            status=200,
            headers={"location": "https://s/" + "a" * 500},
        )
        err = capsys.readouterr().err
        assert "…" in err
        assert "a" * 201 not in err

    def test_token_query_param_redacted_on_line(self, capsys, monkeypatch):
        monkeypatch.delenv("COMIC_DL_TRACE_HTTP", raising=False)
        set_verbosity(DIAGNOSTIC)
        http_event("GET", "https://s/img?token=abc123", status=200)
        err = capsys.readouterr().err
        assert "token=abc123" not in err
        assert "token=***" in err


class TestRedactUrl:
    def test_masks_sensitive_query_params(self):
        url = "https://s/p?a=1&token=abc&key=xyz"
        assert redact_url(url) == "https://s/p?a=1&token=***&key=***"

    def test_passthrough_without_query(self):
        assert redact_url("https://s/plain") == "https://s/plain"

    def test_keeps_benign_params(self):
        assert redact_url("https://s/p?page=2&lang=en") == "https://s/p?page=2&lang=en"


class TestStageLine:
    @pytest.fixture(autouse=True)
    def _reset(self):
        set_verbosity(0)
        yield
        set_verbosity(0)

    def test_tagless_at_verbose(self, capsys):
        set_verbosity(VERBOSE)
        stage_line("Fetching chapter…")
        err = capsys.readouterr().err
        assert "Fetching chapter…" in err
        assert "[scrape]" not in err

    def test_tagged_at_diagnostic(self, capsys):
        set_verbosity(DIAGNOSTIC)
        stage_line("Fetching chapter…")
        assert "[scrape] Fetching chapter…" in capsys.readouterr().err

    def test_hidden_at_normal(self, capsys):
        set_verbosity(NORMAL)
        stage_line("Fetching chapter…")
        assert capsys.readouterr().err == ""


class TestTrace:
    @pytest.fixture(autouse=True)
    def _reset(self):
        set_verbosity(0)
        yield
        set_verbosity(0)

    def test_emitted_at_trace(self, capsys):
        set_verbosity(TRACE)
        trace("retry img: backoff 2.0s")
        assert "retry img: backoff 2.0s" in capsys.readouterr().err

    def test_hidden_below_trace(self, capsys):
        set_verbosity(DIAGNOSTIC)
        trace("internal step")
        assert "internal step" not in capsys.readouterr().err

    def test_stderr_only(self, capsys):
        set_verbosity(TRACE)
        trace("workflow line")
        captured = capsys.readouterr()
        assert "workflow line" in captured.err
        assert "workflow line" not in captured.out


class TestPipeline:
    pytestmark = pytest.mark.asyncio

    async def test_quiet_mode_skips_live(self):
        p = Pipeline(quiet=True)
        async with p:
            assert p._live is None

    async def test_stage_updates_description(self):
        p = Pipeline(quiet=True)
        async with p:
            p.stage("Verifying...")
            assert p._desc == "Verifying..."
            p.stage("Archiving...")
            assert p._desc == "Archiving..."

    async def test_succeed_marks_done(self):
        p = Pipeline(quiet=True)
        async with p:
            await p.succeed("Saved: test.cbz")
        assert p._done is True

    async def test_non_quiet_creates_live(self):
        p = Pipeline(quiet=False)
        async with p:
            assert p._live is not None


class TestPipelineProgress:
    pytestmark = pytest.mark.asyncio

    async def test_show_progress_creates_group(self):
        p = Pipeline(quiet=False)
        async with p:
            p.show_progress(total=10)
            r = p._make_renderable()
            # header + compact bar-with-inline-stats (two-line row)
            assert len(r.renderables) == 2

    async def test_clear_progress_removes_bar(self):
        p = Pipeline(quiet=False)
        async with p:
            p.show_progress(total=10)
            p.clear_progress()
            r = p._make_renderable()
            assert len(r.renderables) == 1

    async def test_update_progress_updates_task(self):
        p = Pipeline(quiet=False)
        async with p:
            p.show_progress(total=10)
            p.update_progress(5)
            task = p._progress.tasks[0]
            assert task.completed == 5

    async def test_quiet_mode_show_progress_noop(self):
        p = Pipeline(quiet=True)
        async with p:
            p.show_progress(total=10)
            assert p._progress is None

    async def test_zero_total_noop(self):
        p = Pipeline(quiet=False)
        async with p:
            p.show_progress(total=0)
            assert p._progress is None

    async def test_succeed_clears_progress(self):
        p = Pipeline(quiet=False)
        async with p:
            p.show_progress(total=10)
            await p.succeed("done")
        assert p._progress is None

    async def test_fail_clears_progress(self):
        p = Pipeline(quiet=False)
        async with p:
            p.show_progress(total=10)
            await p.fail("failed")
        assert p._progress is None

    async def test_close_clears_progress(self):
        p = Pipeline(quiet=False)
        async with p:
            p.show_progress(total=10)
            await p.close()
        assert p._progress is None

    async def test_aexit_clears_progress(self):
        p = Pipeline(quiet=False)
        async with p:
            p.show_progress(total=10)
        assert p._progress is None

    async def test_update_progress_no_task_no_crash(self):
        p = Pipeline(quiet=False)
        async with p:
            p.update_progress(5)


class TestMakeDownloadProgress:
    def test_no_duplicate_percentage_column(self):
        progress = make_download_progress()
        col_types = [type(c).__name__ for c in progress.columns]
        assert "TaskProgressColumn" not in col_types
        assert "BarColumn" in col_types

    def test_has_pages_and_stats_columns(self):
        progress = make_download_progress()
        col_types = [type(c).__name__ for c in progress.columns]
        assert col_types[1] == "TextColumn"
        assert col_types[2] == "_RowStatsColumn"


class TestETA:
    def test_unknown_remaining_shows_placeholder(self):
        from rich.progress import Progress

        progress = Progress()
        column = ETA()
        with progress:
            task_id = progress.add_task("test", total=10)
            task = progress._tasks[task_id]
            result = column.render(task)
            assert result.plain == "--:--"

    def test_complete_shows_empty(self):
        from rich.progress import Progress

        progress = Progress()
        column = ETA()
        with progress:
            task_id = progress.add_task("test", total=10)
            progress.update(task_id, completed=10)
            task = progress._tasks[task_id]
            result = column.render(task)
            assert result.plain == ""

    def test_partial_shows_wall_clock_countdown(self):
        import time

        from rich.progress import Progress

        progress = Progress()
        column = ETA()
        with progress:
            task_id = progress.add_task("test", total=100)
            progress.update(task_id, completed=10)
            time.sleep(0.005)  # ensure elapsed > 0 so an ETA can be computed
            task = progress._tasks[task_id]
            result = column.render(task)
            # Wall-clock estimate means it renders a countdown, never a
            # frozen placeholder or empty cell while work is incomplete.
            assert result.plain not in ("", "--:--")


class TestFormatRemaining:
    def test_minutes_and_seconds(self):
        assert _format_remaining(90) == "01:30"

    def test_hours(self):
        assert _format_remaining(3600 + 12 * 60 + 5) == "1:12:05"

    def test_zero(self):
        assert _format_remaining(0) == "00:00"

    def test_negative_is_safe(self):
        assert _format_remaining(-3) == "00:00"


class TestPrintSkipped:
    def test_skipped_runs(self, capsys):
        print_skipped("test skipped")
        captured = capsys.readouterr()
        assert "test skipped" in captured.err


class TestPrintHelpersEscapeMarkup:
    """Chapter titles and other external strings may contain rich markup
    tokens (``[Director's Cut]`` etc.) and must render literally, never as
    markup or a MarkupError."""

    def test_success_escapes_bracket_title(self, capsys):
        print_success("Ep 1 [Director's Cut]")
        out = capsys.readouterr().out
        assert "Ep 1 [Director's Cut]" in out

    def test_error_escapes_markup(self, capsys):
        print_error("failed: [red]boom[/red]")
        captured = capsys.readouterr()
        assert "[red]boom[/red]" in captured.err

    def test_header_and_meta_escape(self, capsys):
        print_header("Series [One]")
        print_meta("Chapter", "Ch [1]")
        captured = capsys.readouterr()
        assert "Series [One]" in captured.err
        assert "Ch [1]" in captured.err

    def test_dim_and_warning_escape(self, capsys):
        print_dim("note [x]")
        print_warning("careful [y]")
        captured = capsys.readouterr()
        assert "note [x]" in captured.err
        assert "careful [y]" in captured.err


class TestGlyphFallback:
    def test_forced_ascii_glyphs(self, capsys):
        from comic_dl.ui import set_ascii_glyphs

        set_ascii_glyphs(True)
        try:
            print_error("boom")
            print_skipped("nope")
            print_header("Head")
            captured = capsys.readouterr()
            assert "FAIL boom" in captured.err
            assert "- nope" in captured.err
            assert "> Head" in captured.err
        finally:
            set_ascii_glyphs(False)

    def test_utf8_glyphs_default(self, capsys):
        from comic_dl.ui import set_ascii_glyphs

        set_ascii_glyphs(False)
        try:
            print_error("boom")
            assert "✘" in capsys.readouterr().err
        finally:
            set_ascii_glyphs(True)

    def test_env_var_forces_ascii(self, monkeypatch):
        from comic_dl import ui

        monkeypatch.setenv("COMIC_DL_ASCII", "1")
        assert ui._ascii_fallback_enabled() is True
        monkeypatch.delenv("COMIC_DL_ASCII")

    def test_dumb_terminal_forces_ascii(self, monkeypatch):
        from comic_dl import ui

        monkeypatch.delenv("COMIC_DL_ASCII", raising=False)
        monkeypatch.setenv("TERM", "dumb")
        assert ui._ascii_fallback_enabled() is True
        monkeypatch.delenv("TERM")

    def test_spinner_glyphs_follow_ascii(self):
        from comic_dl import ui

        ui.set_ascii_glyphs(True)
        try:
            assert ui.SPINNER_GLYPHS == "|/-\\"
        finally:
            ui.set_ascii_glyphs(False)
        assert "⠋" in ui.SPINNER_GLYPHS

    def test_ascii_table_is_complete_and_byte_stable(self):
        from comic_dl import ui

        for field in ui._GlyphSet.__dataclass_fields__:
            value = getattr(ui._ASCII_GLYPHS, field)
            assert value, f"ASCII glyph {field!r} must not be empty"
            assert all(ord(c) < 128 for c in value), (
                f"ASCII glyph {field!r} is not byte-stable: {value!r}"
            )

    def test_utf8_table_has_no_empty_fields(self):
        from comic_dl import ui

        for field in ui._GlyphSet.__dataclass_fields__:
            assert getattr(ui._UTF8_GLYPHS, field), (
                f"UTF-8 glyph {field!r} must not be empty"
            )

    def test_ascii_table_has_no_banned_unicode(self):
        from comic_dl import ui

        banned = "✔•…█░↓"
        joined = "".join(getattr(ui._ASCII_GLYPHS, f) for f in ui._GlyphSet.__dataclass_fields__)
        assert not any(c in banned for c in joined)

    def test_encoding_supports_utf8(self):
        from comic_dl import ui

        assert ui._encoding_supports_utf8("utf-8") is True
        assert ui._encoding_supports_utf8("UTF8") is True
        assert ui._encoding_supports_utf8("ascii") is False
        assert ui._encoding_supports_utf8("latin-1") is False
        assert ui._encoding_supports_utf8(None) is True


class TestColorEnvResolution:
    """Table-driven coverage of the env precedence in _resolve_env_color.

    Precedence (Prompt.8 decision (c)): NO_COLOR > CLICOLOR_FORCE > CLICOLOR
    > FORCE_COLOR > TERM=dumb > (defer to TTY).
    """

    _ENV_KEYS = ("NO_COLOR", "CLICOLOR", "CLICOLOR_FORCE", "FORCE_COLOR", "TERM")

    def _resolve(self, monkeypatch, **env):
        for key in self._ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return ui_module._resolve_env_color()

    def test_clean_env_defers_to_tty(self, monkeypatch):
        assert self._resolve(monkeypatch) is None

    @pytest.mark.parametrize("value", ["1", "0", "anything"])
    def test_no_color_nonempty_wins(self, monkeypatch, value):
        assert self._resolve(monkeypatch, NO_COLOR=value) == "never"

    def test_no_color_empty_string_ignored(self, monkeypatch):
        assert self._resolve(monkeypatch, NO_COLOR="") is None

    def test_no_color_beats_clicolor_force(self, monkeypatch):
        assert (
            self._resolve(monkeypatch, NO_COLOR="1", CLICOLOR_FORCE="1") == "never"
        )

    def test_clicolor_force_wins(self, monkeypatch):
        assert (
            self._resolve(monkeypatch, CLICOLOR_FORCE="1", CLICOLOR="0") == "always"
        )

    def test_clicolor_zero(self, monkeypatch):
        assert self._resolve(monkeypatch, CLICOLOR="0") == "never"

    def test_clicolor_one(self, monkeypatch):
        assert self._resolve(monkeypatch, CLICOLOR="1") == "always"

    def test_clicolor_garbage_defers_to_tty(self, monkeypatch):
        assert self._resolve(monkeypatch, CLICOLOR="yes") is None

    @pytest.mark.parametrize("depth", ["1", "2", "3", "9"])
    def test_force_color_any_nonzero_is_always(self, monkeypatch, depth):
        assert self._resolve(monkeypatch, FORCE_COLOR=depth) == "always"

    def test_force_color_zero_is_never(self, monkeypatch):
        assert self._resolve(monkeypatch, FORCE_COLOR="0") == "never"

    def test_clicolor_one_beats_force_color_zero(self, monkeypatch):
        assert self._resolve(monkeypatch, CLICOLOR="1", FORCE_COLOR="0") == "always"

    def test_force_color_beats_term_dumb(self, monkeypatch):
        assert self._resolve(monkeypatch, FORCE_COLOR="1", TERM="dumb") == "always"

    @pytest.mark.parametrize("term", ["dumb", "unknown", "DUMB"])
    def test_dumb_terminal_is_never(self, monkeypatch, term):
        assert self._resolve(monkeypatch, TERM=term) == "never"

    def test_plain_terminal_defers_to_tty(self, monkeypatch):
        assert self._resolve(monkeypatch, TERM="xterm-256color") is None


class TestColorSystemPin:
    def _pin(self, mode):
        c = Console()
        ui_module._pin_console_color(c, mode)
        return c

    def test_never_pins_no_color_off_terminal(self):
        c = self._pin("never")
        assert c.no_color is True
        assert c._force_terminal is False

    def test_always_forces_terminal(self):
        c = self._pin("always")
        assert c.no_color is False
        assert c._force_terminal is True

    def test_force_color_2_selects_eight_bit(self, monkeypatch):
        monkeypatch.setenv("FORCE_COLOR", "2")
        assert self._pin("always")._color_system is ColorSystem.EIGHT_BIT

    def test_force_color_3_selects_truecolor(self, monkeypatch):
        monkeypatch.setenv("FORCE_COLOR", "3")
        assert self._pin("always")._color_system is ColorSystem.TRUECOLOR

    def test_force_color_1_selects_standard(self, monkeypatch):
        monkeypatch.setenv("FORCE_COLOR", "1")
        assert self._pin("always")._color_system is ColorSystem.STANDARD

    def test_invalid_force_color_depth_falls_back(self, monkeypatch):
        monkeypatch.setenv("FORCE_COLOR", "9")
        c = self._pin("always")
        assert c._color_system is not None

    def test_auto_restores_rich_detection(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        c = self._pin("auto")
        assert c.no_color is True
        assert c._force_terminal is None


class TestApplyColorMode:
    def test_json_mode_forces_never_even_under_always(self, monkeypatch):
        from comic_dl.ui import console, err_console, set_json_mode

        original = (console.no_color, err_console.no_color)
        try:
            set_json_mode(True)
            ui_module.apply_color_mode("always")
            assert console.no_color is True
            assert err_console.no_color is True
        finally:
            set_json_mode(False)
            console.no_color, err_console.no_color = original


class TestLightBackground:
    def _detect(self, monkeypatch, raw):
        monkeypatch.delenv("COLORFGBG", raising=False)
        if raw is not None:
            monkeypatch.setenv("COLORFGBG", raw)
        return ui_module._detect_light_background()

    def test_absent_defaults_dark(self, monkeypatch):
        assert self._detect(monkeypatch, None) is False

    def test_dark_bg_index_below_eight(self, monkeypatch):
        assert self._detect(monkeypatch, "15;0") is False
        assert self._detect(monkeypatch, "0;7") is False

    def test_light_bg_index_eight_or_more(self, monkeypatch):
        assert self._detect(monkeypatch, "0;15") is True
        assert self._detect(monkeypatch, "0;8") is True

    def test_unparseable_defaults_dark(self, monkeypatch):
        assert self._detect(monkeypatch, "15") is False
        assert self._detect(monkeypatch, "garbage") is False
        assert self._detect(monkeypatch, "15;x") is False


class TestColorTokens:
    ANSI = {
        "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white",
        "bright_black", "bright_red", "bright_green", "bright_yellow",
        "bright_blue", "bright_magenta", "bright_cyan", "bright_white",
    }

    def test_all_roles_resolve_to_ansi_names(self, monkeypatch):
        monkeypatch.setattr(ui_module, "_LIGHT_BACKGROUND", False)
        for role in ui_module._COLOR_ROLES:
            assert ui_module._color_token(role) in self.ANSI, role

    def test_dark_default_brand_and_muted(self, monkeypatch):
        monkeypatch.setattr(ui_module, "_LIGHT_BACKGROUND", False)
        assert ui_module.style("brand") == "bright_yellow"
        assert ui_module.style("muted") == "bright_black"

    def test_light_muted_gets_override(self, monkeypatch):
        monkeypatch.setattr(ui_module, "_LIGHT_BACKGROUND", True)
        assert ui_module.style("muted") == "grey37"
        assert ui_module.style("brand") == "bright_yellow"

    def test_bold_wraps_token(self, monkeypatch):
        monkeypatch.setattr(ui_module, "_LIGHT_BACKGROUND", False)
        assert ui_module.style("brand", bold=True) == "bold bright_yellow"

    def test_constants_follow_dark_tokens(self, monkeypatch):
        monkeypatch.setattr(ui_module, "_LIGHT_BACKGROUND", False)
        assert ui_module.BRAND == "bright_yellow"
        assert ui_module.MUTED == "bright_black"
        assert ui_module.WARNING == "yellow"


class TestBanner:
    def test_whole_logo_uses_one_brand_style(self, monkeypatch):
        captured: list = []

        class _FakeConsole:
            def print(self, *args, **kwargs):
                captured.append((args, kwargs))

            @property
            def width(self):
                return 120

        monkeypatch.setattr(ui_module, "_active_console", lambda: _FakeConsole())
        ui_module.print_banner()

        logos = [
            args[0] for args, _k in captured if args and isinstance(args[0], ui_module.Text)
        ]
        assert logos, "banner printed no logo Text"
        logo = logos[0]
        styles = {span.style for span in logo.spans if span.style is not None}
        # The whole ASCII logo shares a single brand style — no per-line two-tone.
        assert styles == {ui_module._BANNER_LINE_STYLE}


class TestSpeedFormat:
    def test_small_values_use_bytes_per_second(self):
        assert ui_module._format_speed(500) == "500 B/s"

    def test_kib_shows_one_decimal_below_100k(self):
        assert ui_module._format_speed(2048) == "2.0 KB/s"

    def test_kib_shows_whole_number_above_100k(self):
        assert ui_module._format_speed(200 * 1024) == "200 KB/s"

    def test_mebi_shows_one_decimal(self):
        assert ui_module._format_speed(2 * 1024 * 1024) == "2.0 MB/s"


class TestRowStallUI:
    def _fake_task(self):
        return type(
            "Task",
            (),
            {
                "total": 100,
                "completed": 5,
                "finished": False,
                "elapsed": 10.0,
            },
        )()

    def test_stalled_row_hides_speed_and_eta(self):
        state = ui_module.RowState(
            key="k",
            label="k",
            status="running",
            bytes=5 * 1024 * 1024,
            speed_ewma=1024 * 1024,
            started_at=time.monotonic() - 10,
            last_tick=time.monotonic() - ui_module._STALL_AFTER - 1,
        )
        col = ui_module._RowStatsColumn(lambda: state)
        assert col.render(self._fake_task()).plain == ""

    def test_active_row_shows_speed(self):
        state = ui_module.RowState(
            key="k",
            label="k",
            status="running",
            bytes=5 * 1024 * 1024,
            speed_ewma=1024 * 1024,
            started_at=time.monotonic() - 10,
            last_tick=time.monotonic(),
        )
        col = ui_module._RowStatsColumn(lambda: state)
        assert "MB/s" in col.render(self._fake_task()).plain

    def test_running_row_swaps_stage_for_waiting_clause_when_stalled(self):
        state = ui_module.RowState(
            key="k",
            label="k",
            stage="Downloading images...",
            status="running",
            last_tick=time.monotonic() - ui_module._STALL_AFTER - 1,
        )
        group = ui_module._running_row_renderable(state, None, 0)
        header = group.renderables[0]
        assert "waiting for server" in header.plain
        assert "Downloading images" not in header.plain

    def test_running_row_keeps_stage_when_active(self):
        state = ui_module.RowState(
            key="k",
            label="k",
            stage="Downloading images...",
            status="running",
            last_tick=time.monotonic(),
        )
        group = ui_module._running_row_renderable(state, None, 0)
        header = group.renderables[0]
        assert "Downloading images" in header.plain
        assert "waiting for server" not in header.plain


class TestNoHardcodedGlyphs:
    """The UI must route every symbol through the glyph tables.

    Any raw ✔•…█░↓ in a runtime string is a regression: under COMIC_DL_ASCII
    or a non-UTF-8 locale it would mojibake. Only the glyph tables and the
    UTF-8 spinner set may contain them.
    """

    _BANNED = "✔•…█░↓"
    _TABLES = {"_UTF8_GLYPHS", "_ASCII_GLYPHS", "_SPINNER_GLYPHS_UTF8"}

    def _table_subtrees(self, tree):
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in self._TABLES
                for t in node.targets
            ):
                roots.add(id(node.value))
        return roots

    def _walk(self, node, parent, table_roots, offenders, path):
        if id(node) in table_roots:
            return
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if not (isinstance(parent, ast.Expr) and parent.value is node) and any(
                c in node.value for c in self._BANNED
            ):
                offenders.append((path, node.lineno, node.value))
        elif isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    if any(c in value.value for c in self._BANNED):
                        offenders.append((path, value.lineno, value.value))
                elif isinstance(value, ast.FormattedValue):
                    self._walk(value, node, table_roots, offenders, path)
            return
        for child in ast.iter_child_nodes(node):
            self._walk(child, node, table_roots, offenders, path)

    def test_no_hardcoded_glyphs_outside_tables(self):
        package_dir = Path(ui_module.__file__).parent
        offenders = []
        for path in sorted(package_dir.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            roots = self._table_subtrees(tree)
            for node in tree.body:
                self._walk(node, None, roots, offenders, path)
        assert not offenders, offenders[:10]


class TestActivityOverallHeader:
    def test_counts_queued_running_done(self):
        act = Activity(quiet=True)
        act.begin_batch(4)
        act.add_queued_row("u1", label="one")
        act.add_queued_row("u2", label="two")
        act.add_queued_row("u3", label="three")
        act.add_queued_row("u4", label="four")
        act.mark_running("u1", stage="working")
        act.finish_row("u2", ok=True, message="ok")
        header = act._overall_renderable()
        assert header is not None
        plain = header.plain
        assert "Overall" in plain
        assert "1/4" in plain
        assert "1 running" in plain
        assert "2 queued" in plain
        act.finish_row("u1", ok=True, message="ok")
        assert "2/4" in act._overall_renderable().plain
        assert "2 queued" in act._overall_renderable().plain
        act.finish_row("u3", ok=True, message="ok")
        act.finish_row("u4", ok=True, message="ok")
        final = act._overall_renderable().plain
        assert "100%" in final
        assert "0 running" in final
        # The queued slot is always rendered (0 queued in dim) so the line
        # keeps a stable width instead of shifting the size/ETA columns.
        assert "0 queued" in final

    def test_no_header_without_batch(self):
        act = Activity(quiet=True)
        assert act._overall_renderable() is None

    def test_skipped_rows_land_in_completed_section(self):
        act = Activity(quiet=True)
        act.begin_batch(2)
        act.add_queued_row("u1", label="one")
        act.add_queued_row("u2", label="two")
        act.finish_row("u1", ok=True, message="already downloaded")
        completed = act._completed_renderable()
        assert completed is not None
        assert any("already downloaded" in r.plain for r in completed.renderables)

    def test_completed_section_shows_bytes(self):
        act = Activity(quiet=True)
        act.add_queued_row("u1", label="one")
        act.mark_running("u1")
        st = act._rows["u1"]
        st.status = "done"
        st.ok = True
        st.pages = 20
        st.bytes = 5 * 1024 * 1024
        completed = act._completed_renderable()
        assert completed is not None
        assert any("5 MB" in r.plain for r in completed.renderables)


class TestBatchETA:
    def _states(self, rows):
        from comic_dl.ui import RowState

        return [
            RowState(key=k, status=s, total=t, bytes=b) for k, s, t, b in rows
        ]

    def test_page_weighted_projection(self):
        from comic_dl.ui import _batch_eta

        states = self._states(
            [
                ("big", "done", 150, 15 * 1024 * 1024),
                ("small", "running", 10, 0),
            ]
        )
        eta = _batch_eta(
            states, done=1, batch_total=2, nbytes=15 * 1024 * 1024,
            speed=1024 * 1024,
        )
        # 160 pages total at 1 MB/s -> 16 s, minus the 15 MB already fetched.
        assert eta == pytest.approx(1.0)

    def test_url_count_fallback_when_pages_unknown(self):
        from comic_dl.ui import _batch_eta

        states = self._states(
            [
                ("a", "done", 0, 5 * 1024 * 1024),
                ("b", "running", 0, 0),
            ]
        )
        eta = _batch_eta(
            states, done=1, batch_total=2, nbytes=5 * 1024 * 1024,
            speed=1024 * 1024,
        )
        assert eta == pytest.approx(5.0)

    def test_none_when_batch_complete(self):
        from comic_dl.ui import _batch_eta

        states = self._states([("a", "done", 10, 1024 * 1024)])
        assert (
            _batch_eta(
                states, done=1, batch_total=1, nbytes=1024 * 1024,
                speed=1024 * 1024,
            )
            is None
        )


class TestPrintBatchSummary:
    def test_all_success(self, capsys):
        print_batch_summary(2, 0, 0, [])
        captured = capsys.readouterr()
        assert "All 2 URLs completed successfully." in captured.out

    def test_with_skipped(self, capsys):
        print_batch_summary(2, 1, 0, [])
        captured = capsys.readouterr()
        assert "All 3 URLs completed successfully (1 skipped)." in captured.out

    def test_with_failures(self, capsys):
        print_batch_summary(1, 1, 1, ["https://x/2/"])
        captured = capsys.readouterr()
        assert "Processed 3 URLs: 1 downloaded, 1 skipped, 1 failed" in captured.err
        assert "https://x/2/" in captured.err

    def test_with_failure_details_uses_grouped_recap(self, capsys):
        print_batch_summary(
            1, 0, 2,
            ["Failed: https://a/", "Failed: https://b/"],
            failure_details=[
                ("Failed: https://a/", "Unsupported URL for domain 'x'."),
                ("Failed: https://b/", "Unsupported URL for domain 'x'."),
            ],
        )
        captured = capsys.readouterr()
        assert "Processed 3 URLs: 1 downloaded, 0 skipped, 2 failed" in captured.err
        # Grouped: one reason line with a count, both labels listed once each.
        assert captured.err.count("Unsupported URL for domain 'x'.") == 1
        assert "x2" in captured.err
        assert captured.err.count("Failed: https://a/") == 1
        assert captured.err.count("Failed: https://b/") == 1


def _verdict_line(out: str) -> str:
    """The summary verdict line (the only 'Download...' line without a colon)."""
    for line in out.splitlines():
        if "Download" in line and ":" not in line:
            return line.strip()
    return ""


class TestPrintSummary:
    def test_default(self, capsys):
        print_summary("S", 5, 1, 0, "/out", "10s")
        captured = capsys.readouterr()
        assert "Download complete" in captured.out

    def test_with_bytes_and_throughput(self, capsys):
        print_summary("S", 5, 1, 0, "/out", "10s",
                      total_bytes=50 * 1024 * 1024, elapsed_secs=10.0)
        captured = capsys.readouterr()
        assert "MB" in captured.out
        assert "MB/s" in captured.out

    def test_failures_show_error_verdict_not_success(self, capsys):
        print_summary("S", 1, 0, 1, "/out", "10s")
        captured = capsys.readouterr()
        assert "completed with errors" in _verdict_line(captured.out)
        assert "(1 failed)" in captured.out

    def test_partial_shows_error_verdict_not_success(self, capsys):
        print_summary("S", 0, 0, 0, "/out", "10s", partial=1)
        captured = capsys.readouterr()
        assert "incomplete" in _verdict_line(captured.out)
        assert "(1 partial)" in captured.out

    def test_partial_and_failed_both_listed(self, capsys):
        print_summary("S", 2, 0, 1, "/out", "10s", partial=2)
        captured = capsys.readouterr()
        assert "completed with errors" in _verdict_line(captured.out)
        assert "(2 partial, 1 failed)" in captured.out

    def test_interrupted_never_claims_success(self, capsys):
        print_summary("S", 0, 27, 0, "/out", "10s", interrupted=True)
        captured = capsys.readouterr()
        assert "Interrupted" in captured.out
        assert "Download complete" not in captured.out

    def test_interrupted_with_failures_still_leads_with_interrupt(self, capsys):
        print_summary("S", 1, 0, 2, "/out", "10s", partial=1, interrupted=True)
        captured = capsys.readouterr()
        assert "Interrupted" in captured.out
        assert "Download complete" not in captured.out
        assert "(1 partial, 2 failed)" in captured.out


class TestPrintChapterPreview:
    def test_preview_runs(self, capsys):
        chapters = [{"title": f"Chapter {i}", "episode_no": str(i)} for i in range(3)]
        print_chapter_preview(chapters, 3, "Test Series")
        captured = capsys.readouterr()
        assert "Test Series" in captured.err
        assert "Chapter" in captured.err

    def test_empty_chapters(self, capsys):
        print_chapter_preview([], 0, "")
        captured = capsys.readouterr()
        assert captured.out == "" and captured.err == ""


class TestPrintFailureRecap:
    def test_recap_with_failures(self, capsys):
        print_failure_recap([("Chapter 1", "HTTP 404"), ("Chapter 2", "timeout")])
        captured = capsys.readouterr()
        assert "Failed" in captured.err
        assert "Chapter 1" in captured.err
        assert "HTTP 404" in captured.err

    def test_recap_groups_identical_reasons(self, capsys):
        print_failure_recap([
            ("Failed: https://a/", "Unsupported URL for domain 'fsicomics.com'."),
            ("Failed: https://b/", "Unsupported URL for domain 'fsicomics.com'."),
            ("Failed: https://c/", "Could not connect to the server."),
        ])
        captured = capsys.readouterr()
        # One reason bullet with a xN count, not N repeated lines.
        assert captured.err.count("Unsupported URL for domain 'fsicomics.com'.") == 1
        assert "x2" in captured.err
        assert captured.err.count("Failed: https://a/") == 1
        assert captured.err.count("Failed: https://b/") == 1
        assert captured.err.count("Failed: https://c/") == 1
        assert captured.err.count("Could not connect to the server.") == 1

    def test_recap_single_failure_has_no_count(self, capsys):
        print_failure_recap([("Failed: https://a/", "HTTP 404")])
        captured = capsys.readouterr()
        assert "x2" not in captured.err
        assert "Failed: https://a/" in captured.err
        assert "HTTP 404" in captured.err

    def test_empty_failures(self, capsys):
        print_failure_recap([])
        captured = capsys.readouterr()
        assert captured.out == "" and captured.err == ""


class TestDecodeKey:
    def test_arrows(self):
        assert _decode_key(b"\x1b[A") == "up"
        assert _decode_key(b"\x1b[B") == "down"
        assert _decode_key(b"\x1b[C") == "right"
        assert _decode_key(b"\x1b[D") == "left"

    def test_space_and_enter(self):
        assert _decode_key(b" ") == "space"
        assert _decode_key(b"\r") == "enter"
        assert _decode_key(b"\n") == "enter"

    def test_ctrl_c_and_esc(self):
        assert _decode_key(b"\x03") == "ctrl-c"
        assert _decode_key(b"\x1b") == "esc"

    def test_letters(self):
        assert _decode_key(b"a") == "a"
        assert _decode_key(b"A") == "A"
        assert _decode_key(b"q") == "q"
        assert _decode_key(b"j") == "j"
        assert _decode_key(b"k") == "k"

    def test_printables_pass_through(self):
        assert _decode_key(b"w") == "w"
        assert _decode_key(b"B") == "B"
        assert _decode_key(b"2") == "2"
        assert _decode_key(b"-") == "-"
        assert _decode_key(b".") == "."

    def test_backspace(self):
        assert _decode_key(b"\x7f") == "backspace"
        assert _decode_key(b"\x08") == "backspace"

    def test_unknown(self):
        assert _decode_key(b"\x00") == "unknown"
        assert _decode_key(b"\xff") == "unknown"


class TestCheckboxPrompt:
    def _run(self, keys, options, **kwargs):
        console_obj = Console(file=io.StringIO(), width=80, force_terminal=True)
        reader = iter(keys)
        return checkbox_prompt(
            "Pick",
            options,
            read_key=lambda: next(reader),
            console_obj=console_obj,
            **kwargs,
        )

    def test_confirm_empty_returns_empty_set(self):
        assert self._run(["enter"], [(1, "One"), (2, "Two")]) == set()

    def test_space_toggles_and_enter_confirms(self):
        assert self._run(["down", "space", "enter"], [(1, "One"), (2, "Two")]) == {2}

    def test_select_all_key(self):
        result = self._run(["a", "enter"], [(1, "One"), (2, "Two"), (3, "Three")])
        assert result == {1, 2, 3}

    def test_toggle_off(self):
        assert self._run(["space", "space", "enter"], [(1, "One"), (2, "Two")]) == set()

    def test_q_cancels(self):
        assert self._run(["down", "space", "q"], [(1, "One"), (2, "Two")]) is None

    def test_esc_cancels(self):
        assert self._run(["esc"], [(1, "One")]) is None

    def test_ctrl_c_interrupts(self):
        """Ctrl-C is an interrupt (exit 130), not a clean cancel like q/Esc."""

        with pytest.raises(KeyboardInterrupt):
            self._run(["ctrl-c"], [(1, "One"), (2, "Two")])

    def test_eof_cancels_instead_of_spinning(self):
        """stdin EOF (terminal closed) must stop the prompt, not busy-loop."""

        def eof():
            raise EOFError

        console_obj = Console(file=io.StringIO(), width=80, force_terminal=True)
        result = checkbox_prompt(
            "Pick", [(1, "One"), (2, "Two")],
            read_key=eof,
            console_obj=console_obj,
        )
        assert result is None

    def test_multiple_selections(self):
        result = self._run(
            ["space", "down", "space", "down", "space", "enter"],
            [(1, "A"), (2, "B"), (3, "C")],
        )
        assert result == {1, 2, 3}

    def test_jk_navigation(self):
        assert self._run(["j", "space", "enter"], [(1, "One"), (2, "Two")]) == {2}

    def test_scroll_bounds_no_crash(self):
        options = [(i, f"Chapter {i}") for i in range(1, 21)]
        keys = ["down"] * 25 + ["space", "enter"]
        assert self._run(keys, options, height=5) == {20}

    def test_render_shows_glyphs(self):
        from comic_dl.ui import glyphs

        console_obj = Console(file=io.StringIO(), width=80, force_terminal=True)
        reader = iter(["space", "enter"])
        checkbox_prompt(
            "Pick", [(1, "One"), (2, "Two")],
            read_key=lambda: next(reader),
            console_obj=console_obj,
        )
        out = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", console_obj.file.getvalue())
        out = re.sub(r"\s+", " ", out)
        assert f"{glyphs().radio_on} 1. One" in out
        assert f"{glyphs().radio_off} 2. Two" in out


class TestCheckboxRenderable:
    def test_cursor_marker_and_glyph(self):
        from comic_dl.ui import glyphs

        group = _checkbox_renderable(
            "Pick", [(1, "One"), (2, "Two")], {1}, cursor=0, view_start=0, height=2,
        )
        plain = "\n".join(r.plain for r in group.renderables)
        assert glyphs().radio_on in plain
        assert glyphs().radio_off in plain
        assert "Pick" in plain

    def test_footer_hint(self):
        group = _checkbox_renderable(
            "Pick", [(1, "One")], set(), cursor=0, view_start=0, height=1,
        )
        assert any("space toggle" in r.plain for r in group.renderables)

    def test_long_labels_stay_single_line(self):
        console_obj = Console(file=io.StringIO(), width=40, force_terminal=True)
        group = _checkbox_renderable(
            "Pick",
            [(1, "A" * 80), (2, "B" * 80)],
            set(),
            cursor=0,
            view_start=0,
            height=2,
        )
        console_obj.print(group)
        lines = [line for line in console_obj.file.getvalue().splitlines() if line.strip()]
        # title + 2 rows + footer: labels must truncate, never wrap into
        # extra lines (wrapping is what made the selector drift sideways).
        assert len(lines) == 4


def _source_rows() -> list[SourceRow]:
    return [
        SourceRow("asurascans.com", "asurascans", ("series", "chapter"), "built-in"),
        SourceRow("e-hentai.org", "e-hentai", ("chapter",), "built-in"),
        SourceRow("webtoons.com", "webtoons", ("series", "chapter"), "built-in"),
    ]


class TestRenderSourcesTable:
    def _render(self, rows=None, note=True):
        buf = io.StringIO()
        render_sources_table(
            _source_rows() if rows is None else rows,
            console_obj=Console(file=buf, width=80, force_terminal=True),
            note=note,
        )
        out = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", buf.getvalue())
        return out

    def test_columns_and_rows(self):
        out = self._render()
        assert "Supported sources (3)" in out
        assert "SOURCE" in out
        assert "ORIGIN" in out
        assert "asurascans.com" in out
        assert "built-in" in out
        assert "CAPABILITIES" not in out
        assert "series" not in out

    def test_plugin_note_can_be_suppressed(self):
        assert "docs/usage/plugins.md" in self._render(note=True)
        assert "docs/usage/plugins.md" not in self._render(note=False)

    def test_empty_capabilities(self):
        out = self._render([SourceRow("x.org", "x", (), "plugin")])
        assert "x.org" in out
        assert "plugin" in out


class TestSourceSearch:
    def _run(self, keys, rows=None, **kwargs):
        buf = io.StringIO()
        console_obj = Console(file=buf, width=80, force_terminal=True)
        reader = iter(keys)
        code = source_search(
            _source_rows() if rows is None else rows,
            read_key=lambda: next(reader),
            console_obj=console_obj,
            **kwargs,
        )
        return code, buf.getvalue()

    def _plain(self, out):
        out = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", out)
        return re.sub(r"\s+", " ", out)

    def test_q_quits_zero(self):
        code, _ = self._run(["q"])
        assert code == 0

    def test_ctrl_c_interrupts(self):
        code, _ = self._run(["ctrl-c"])
        assert code == 130

    def test_eof_quits_zero(self):
        def eof():
            raise EOFError

        buf = io.StringIO()
        code = source_search(
            _source_rows(),
            read_key=eof,
            console_obj=Console(file=buf, width=80, force_terminal=True),
        )
        assert code == 0

    def test_empty_rows_returns_zero_without_keys(self):
        code = source_search(
            [],
            read_key=lambda: (_ for _ in ()).throw(AssertionError("no keys expected")),
            console_obj=Console(file=io.StringIO(), width=80, force_terminal=True),
        )
        assert code == 0

    def test_typing_filters_rows(self):
        _, out = self._run(["w", "e", "b", "q"])
        plain = self._plain(out)
        assert "webtoons.com" in plain
        assert "e-hentai.org" not in plain

    def test_esc_clears_filter(self):
        _, out = self._run(["w", "e", "b", "esc", "q"])
        plain = self._plain(out)
        assert "webtoons.com" in plain
        assert "e-hentai.org" in plain

    def test_backspace_edits_query(self):
        _, out = self._run(["w", "e", "b", "backspace", "backspace", "backspace", "q"])
        plain = self._plain(out)
        assert "asurascans.com" in plain
        assert "webtoons.com" in plain
        assert "e-hentai.org" in plain

    def test_no_match_line(self):
        _, out = self._run(["z", "z", "z", "q"])
        plain = self._plain(out)
        assert "No matches" in plain

    def test_filtered_out_counter(self):
        _, out = self._run(["w", "e", "b", "q"])
        plain = self._plain(out)
        assert "2 filtered out" in plain

    def test_scroll_stays_in_bounds(self):
        rows = [SourceRow(f"site{i}.com", f"site{i}", ("chapter",), "built-in") for i in range(20)]
        code, _ = self._run(["down"] * 40 + ["q"], rows=rows, height=5)
        assert code == 0

    def test_render_shows_cursor_and_domain(self):
        from comic_dl.ui import glyphs

        _, out = self._run(["q"])
        plain = self._plain(out)
        assert glyphs().arrow in plain
        assert "asurascans.com" in plain
        assert "built-in" in plain
        assert "/ " in plain
        assert "series" not in plain
        assert "webtoons ·" not in plain


class TestVersionConstant:
    def test_version_exists(self):
        from comic_dl import __version__
        assert isinstance(__version__, str)
        assert len(__version__) > 0


class TestActivityRowBlock:
    def _row(self):
        from comic_dl.ui import Activity
        act = Activity(quiet=False)
        act.row("chapters")
        act.set_label("chapters", "Ep. 2  (2/3)")
        act.set_status("chapters", "Downloading images...")
        act.show_progress("chapters", total=108)
        act.update_progress("chapters", 65)
        return act._row_renderable("chapters")

    def test_header_bar_two_lines(self):
        from rich.padding import Padding
        from rich.progress import Progress

        def plain(renderable) -> str:
            if isinstance(renderable, Padding):
                return plain(renderable.renderable)
            return getattr(renderable, "plain", "")

        r = self._row()
        texts = [plain(g) for g in r.renderables]
        assert len(texts) == 2
        assert any("Downloading images..." in t and "Ep. 2  (2/3)" in t for t in texts)
        # Second line is the compact bar; no wall-clock timing line.
        assert isinstance(r.renderables[1], Padding)
        assert isinstance(r.renderables[1].renderable, Progress)
        assert not any("elapsed" in t for t in texts)


class _FakeLive:
    """Records update/refresh calls instead of talking to a terminal."""

    def __init__(self):
        self.updates: list[tuple[bool, object]] = []

    def update(self, renderable, *, refresh=False):
        self.updates.append((refresh, renderable))

    def refresh(self):
        pass

    def stop(self):
        pass

    def start(self):
        pass


class _BoomLive(_FakeLive):
    """A Live whose update() raises, simulating a render failure mid-run."""

    def update(self, renderable, *, refresh=False):
        raise RuntimeError("render boom")


def _patch_sleep_to_finish(monkeypatch, obj, attr="_done"):
    """Run exactly one _spin/_drive iteration by ending the loop on its first
    sleep: the next `while not done` check sees the flag and exits."""
    real_sleep = asyncio.sleep

    def fake_sleep(seconds):
        setattr(obj, attr, True)
        return real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)


class TestRenderLoopOwner:
    """Each Live has a single frame owner; state events never paint directly."""

    def test_pipeline_default_desc_never_blank(self):
        assert Pipeline(quiet=False)._desc.startswith("Preparing")

    @pytest.mark.asyncio
    async def test_pipeline_spin_is_single_renderer(self, monkeypatch):
        p = Pipeline(quiet=False)
        fake = _FakeLive()
        p._live = fake
        _patch_sleep_to_finish(monkeypatch, p)
        await p._spin()
        assert len(fake.updates) == 1
        assert fake.updates[0][0] is True

    @pytest.mark.asyncio
    async def test_activity_events_never_paint_directly(self, monkeypatch):
        act = Activity(quiet=True)
        fake = _FakeLive()
        act._live = fake
        act.begin_batch(1)
        act.add_queued_row("u1")
        act.mark_running("u1", stage="working")
        act.show_progress("u1", total=10)
        for i in range(100):
            act.update_progress("u1", i)
        assert fake.updates == []
        # A single spin tick coalesces all of those events into one frame.
        _patch_sleep_to_finish(monkeypatch, act)
        await act._spin()
        assert len(fake.updates) == 1
        assert fake.updates[0][0] is True

    @pytest.mark.asyncio
    async def test_activity_spin_idles_when_clean(self, monkeypatch):
        act = Activity(quiet=True)
        fake = _FakeLive()
        act._live = fake
        act.begin_batch(1)
        act.add_queued_row("u1")
        act.mark_running("u1", stage="working")
        act.finish_row("u1", ok=True, message="ok")
        _patch_sleep_to_finish(monkeypatch, act)
        await act._spin()
        assert len(fake.updates) == 1  # first frame picks up the final state
        fake.updates.clear()
        act._done = False
        await act._spin()  # clean and nothing running: no work, no paint
        assert fake.updates == []

    @pytest.mark.asyncio
    async def test_activity_spin_animates_running_row(self, monkeypatch):
        act = Activity(quiet=True)
        fake = _FakeLive()
        act._live = fake
        act.begin_batch(1)
        act.add_queued_row("u1")
        act.mark_running("u1", stage="working")
        act._dirty = False  # no pending state change
        _patch_sleep_to_finish(monkeypatch, act)
        await act._spin()
        # The spinner glyph must keep animating while a row is running.
        assert len(fake.updates) == 1

    @pytest.mark.asyncio
    async def test_spin_survives_render_failure(self, monkeypatch):
        act = Activity(quiet=True)
        act._live = _BoomLive()
        act.begin_batch(1)
        act.add_queued_row("u1")
        act.mark_running("u1", stage="working")
        _patch_sleep_to_finish(monkeypatch, act)
        await act._spin()  # must not raise, must complete the loop normally
        assert act._frame == 1
        assert act._dirty is False

    @pytest.mark.asyncio
    async def test_pipeline_update_progress_clamps(self):
        p = Pipeline(quiet=False)
        async with p:
            p.show_progress(total=10)
            p.update_progress(99)
            assert p._progress.tasks[0].completed == 10

    @pytest.mark.asyncio
    async def test_activity_update_progress_clamps(self):
        act = Activity(quiet=False)
        act.begin_batch(1)
        act.add_queued_row("u1")
        act.mark_running("u1", stage="working")
        act.show_progress("u1", total=10)
        act.update_progress("u1", 99)
        assert act._rows["u1"].done == 10
        assert act._progress["u1"].tasks[0].completed == 10

    def test_activity_update_progress_zero_total_safe(self):
        act = Activity(quiet=True)
        act.add_queued_row("u1")
        act.mark_running("u1", stage="working")
        act.update_progress("u1", 5)
        assert act._rows["u1"].done == 0


class TestLiveTeardown:
    @pytest.mark.asyncio
    async def test_cancel_tears_down_activity_live(self):
        act = Activity(quiet=False)

        async def run():
            async with act:
                act.begin_batch(1)
                act.add_queued_row("u1")
                act.mark_running("u1", stage="working")
                await asyncio.sleep(30)

        task = asyncio.create_task(run())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ui_module._ACTIVE_LIVE is None
        assert act._spin_task is None
        assert act._done is True

    @pytest.mark.asyncio
    async def test_cancel_tears_down_pipeline_live(self):
        p = Pipeline(quiet=False)

        async def run():
            async with p:
                p.stage("working")
                await asyncio.sleep(30)

        task = asyncio.create_task(run())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ui_module._ACTIVE_LIVE is None
        assert p._spin_task is None
        assert p._done is True

    @pytest.mark.asyncio
    async def test_succeed_and_fail_unregister(self):
        p = Pipeline(quiet=False)
        async with p:
            await p.succeed("done")
        assert p._live is None
        assert ui_module._ACTIVE_LIVE is None
        async with p:
            await p.fail("nope")
        assert p._live is None
        assert ui_module._ACTIVE_LIVE is None


class TestFixedWidthLiveSlots:
    """Numeric live columns keep a constant width as values tick, so rows never
    shift side-to-side between frames."""

    def _fake_task(self):
        return type(
            "Task",
            (),
            {
                "total": 100,
                "completed": 5,
                "finished": False,
                "elapsed": 10.0,
            },
        )()

    def test_bytes_slot_fixed_width(self):
        for b in [512 * 1024, 999 * 1024, 1024 * 1024, 100 * 1024 * 1024,
                  1024 * 1024 * 1024, 3 * 1024 * 1024 * 1024]:
            assert len(ui_module.format_bytes_fixed(b)) == 8

    def test_speed_slot_fixed_width(self):
        for s in [500, 2048, 100 * 1024, 2 * 1024 * 1024,
                  100 * 1024 * 1024, 1024 * 1024 * 1024]:
            assert len(ui_module.format_speed_fixed(s)) == 10

    def test_remaining_slot_fixed_width(self):
        for r in [1, 59, 60, 3599, 3600, 12 * 3600 + 5]:
            assert len(ui_module.format_remaining_fixed(r)) == 8

    def test_elapsed_slot_fixed_width(self):
        for e in [0.1, 1.2, 12.3, 99.9, 999.9]:
            assert len(ui_module.format_elapsed_fixed(e)) == 8

    def test_pct_slot_fixed_width(self):
        for p in [0, 5, 42, 99, 100]:
            assert len(f"{p:3.0f}%") == 4

    def test_row_stats_column_stable_width(self):
        state = ui_module.RowState(
            key="k",
            label="k",
            status="running",
            started_at=time.monotonic() - 10,
            last_tick=time.monotonic(),
        )
        col = ui_module._RowStatsColumn(lambda: state)
        widths = set()
        for b in [512 * 1024, 1024 * 1024, 5 * 1024 * 1024, 100 * 1024 * 1024,
                  3 * 1024 * 1024 * 1024]:
            for s in [2048, 2 * 1024 * 1024, 50 * 1024 * 1024]:
                state.bytes = b
                state.speed_ewma = s
                widths.add(len(col.render(self._fake_task()).plain))
        assert len(widths) == 1

    def test_stall_clause_stable_width(self):
        def width(stall_seconds):
            state = ui_module.RowState(
                key="k",
                label="k",
                status="running",
                last_tick=time.monotonic() - stall_seconds,
            )
            return len(ui_module._running_row_renderable(state, None, 0)
                       .renderables[0].plain)

        assert width(50) == width(120) == width(999)


class TestPrintHelp:
    def test_help_lists_verbose_and_list_sources(self, capsys):
        from comic_dl.ui import print_help

        print_help()
        out = capsys.readouterr().out
        assert "-v, -vv, -vvv" in out
        assert "--list-sources" in out

    def test_help_lists_no_banner_and_debug_file(self, capsys):
        from comic_dl.ui import print_help

        print_help()
        out = capsys.readouterr().out
        assert "--no-banner" in out
        assert "--debug-file" in out

    def test_help_lists_chapter_parallel(self, capsys):
        from comic_dl.ui import print_help

        print_help()
        out = capsys.readouterr().out
        assert "--chapter-parallel" in out

    def test_help_lists_update_command(self, capsys):
        from comic_dl.ui import print_help

        print_help()
        out = capsys.readouterr().out
        assert "update <SERIES|all>" in out.replace("\n", " ")

    def test_help_list_sources_line_is_not_glued(self, capsys):
        from comic_dl.ui import print_help

        print_help()
        out = " ".join(capsys.readouterr().out.replace("\n", " ").split())
        assert "[QUERY]" in out and "Search supported sites" in out

    def test_help_mentions_help_subcommand(self, capsys):
        from comic_dl.ui import print_help

        print_help()
        out = " ".join(capsys.readouterr().out.replace("\n", " ").split())
        assert "comic-dl help <command>" in out

    def test_help_points_to_config_and_completion(self, capsys):
        from comic_dl.ui import print_help

        print_help()
        out = capsys.readouterr().out
        assert "config" in out
        assert "completion" in out
        assert "docs/reference/cli.md" in out

    def test_help_lists_no_color(self, capsys):
        from comic_dl.ui import print_help

        print_help()
        out = capsys.readouterr().out
        assert "--no-color" in out

    def test_help_shows_usage_lines(self, capsys):
        from comic_dl.ui import print_help

        print_help()
        out = capsys.readouterr().out
        assert "comic-dl [OPTIONS]" in out
        assert "comic-dl [URL]" in out
        assert "comic-dl -u <URL> [OPTIONS]" in out

    def test_help_shows_purpose_groups(self, capsys):
        from comic_dl.ui import print_help

        print_help()
        out = capsys.readouterr().out
        assert "Download:" in out
        assert "Library:" in out
        assert "Manage:" in out
        assert "Inspect & integrate:" in out

    def test_help_shows_option_categories(self, capsys):
        from comic_dl.ui import print_help

        print_help()
        out = capsys.readouterr().out
        assert "Layout & output:" in out
        assert "Download tuning:" in out
        assert "HTTP & politeness:" in out
        assert "Display:" in out

    def test_help_shows_advanced_flags(self, capsys):
        from comic_dl.ui import print_help

        print_help()
        out = capsys.readouterr().out
        assert "--compress" in out
        assert "--format" in out

    def test_help_shows_exit_status(self, capsys):
        from comic_dl.ui import print_help

        print_help()
        out = capsys.readouterr().out
        assert "Exit status:" in out
        assert "130  interrupted" in out

    def test_help_shows_defaults(self, capsys):
        from comic_dl.ui import print_help

        print_help()
        out = capsys.readouterr().out
        assert "[default: 5]" in out
        assert "chrome146" in out

    def test_help_shows_choices(self, capsys):
        from comic_dl.ui import print_help

        print_help()
        out = capsys.readouterr().out
        assert "[possible values:" in out

    def test_help_shows_aliases_note(self, capsys):
        from comic_dl.ui import print_help

        print_help()
        out = capsys.readouterr().out
        assert "Aliases:" in out


class TestPrintHelpSummary:
    def test_summary_shows_usage_lines(self, capsys):
        from comic_dl.ui import print_help_summary

        print_help_summary()
        out = capsys.readouterr().out
        assert "comic-dl [OPTIONS]" in out
        assert "comic-dl [URL]" in out
        assert "comic-dl -u <URL> [OPTIONS]" in out

    def test_summary_shows_purpose_groups(self, capsys):
        from comic_dl.ui import print_help_summary

        print_help_summary()
        out = capsys.readouterr().out
        assert "Download:" in out
        assert "Library:" in out
        assert "Manage:" in out
        assert "Inspect & integrate:" in out

    def test_summary_shows_option_categories(self, capsys):
        from comic_dl.ui import print_help_summary

        print_help_summary()
        out = capsys.readouterr().out
        assert "Layout & output:" in out
        assert "Download tuning:" in out
        assert "HTTP & politeness:" in out
        assert "Display:" in out

    def test_summary_omits_verbosity_ladder(self, capsys):
        from comic_dl.ui import print_help_summary

        print_help_summary()
        out = capsys.readouterr().out
        assert "0 normal" not in out
        assert "COMIC_DL_TRACE_HTTP" not in out

    def test_summary_omits_exit_status(self, capsys):
        from comic_dl.ui import print_help_summary

        print_help_summary()
        out = capsys.readouterr().out
        assert "Exit status:" not in out

    def test_summary_omits_full_reference(self, capsys):
        from comic_dl.ui import print_help_summary

        print_help_summary()
        out = capsys.readouterr().out
        assert "Full reference:" not in out

    def test_summary_shows_commands_and_options(self, capsys):
        from comic_dl.ui import print_help_summary

        print_help_summary()
        out = capsys.readouterr().out
        assert "--list-sources" in out
        assert "--concurrency" in out
        assert "--no-color" in out
        assert "completion" in out

    def test_summary_shows_pointer(self, capsys):
        from comic_dl.ui import print_help_summary

        print_help_summary()
        out = " ".join(capsys.readouterr().out.replace("\n", " ").split())
        assert "comic-dl help <command>" in out

    def test_full_help_shows_verbosity_ladder(self, capsys):
        from comic_dl.ui import print_help

        print_help()
        out = capsys.readouterr().out
        assert "0 normal" in out
        assert "COMIC_DL_TRACE_HTTP" in out


class TestParserHelp:
    def test_renders_positionals_options_and_defaults(self, capsys):
        import argparse

        from comic_dl.ui import print_parser_help

        parser = argparse.ArgumentParser(prog="comic-dl demo")
        parser.add_argument("series", help="Series to act on")
        parser.add_argument("-n", "--days", type=int, default=7, help="Show last N days")
        parser.add_argument("-q", "--quiet", action="store_true", help="Suppress output")
        print_parser_help(parser)
        out = " ".join(capsys.readouterr().out.replace("\n", " ").split())
        assert "comic-dl demo" in out
        assert "series SERIES" in out
        assert "--days" in out
        assert "[default: 7]" in out
        assert "--quiet" in out

    def test_renders_subcommands(self, capsys):
        import argparse

        from comic_dl.ui import print_parser_help

        parser = argparse.ArgumentParser(prog="comic-dl cookie")
        sub = parser.add_subparsers(dest="action", required=True)
        sub.add_parser("ls", help="list stored cookies")
        sub.add_parser("clear", help="clear cookies")
        print_parser_help(parser)
        out = capsys.readouterr().out
        assert "Commands:" in out
        assert "ls" in out
        assert "list stored cookies" in out
        assert "clear" in out


class TestHeaderFilter:
    def test_keeps_relevant_drops_noise(self):
        from comic_dl.ui import _filter_headers

        lines = _filter_headers(
            {
                "content-type": "image/webp",
                "content-length": "258364",
                "cf-ray": "a2a84a507d634496-SIN",
                "server": "cloudflare",
                "alt-svc": 'h3=":443"',
                "speculation-rules": "/cdn-cgi/speculation",
                "report-to": '{"group":"cf-nel"}',
                "x-turbo-charged-by": "LiteSpeed",
            }
        )
        out = " ".join(lines)
        assert "content-type" in out
        assert "content-length" in out
        assert "cf-ray" in out
        assert "server" not in out
        assert "alt-svc" not in out
        assert "speculation-rules" not in out
        assert "report-to" not in out
        assert "x-turbo-charged-by" not in out

    def test_sensitive_value_masked_when_kept(self):
        from comic_dl.ui import _filter_headers

        out = " ".join(_filter_headers({"etag": "abc"}))
        assert "etag: abc" in out


class TestVerbosityMonotonic:
    @pytest.fixture
    def levels(self, capsys, monkeypatch):
        """Emit a fixed set of lines at each verbosity and capture stderr."""
        from comic_dl.ui import http_event, set_verbosity, stage_line, trace

        captured = {}
        for name, level in (("v", VERBOSE), ("vv", DIAGNOSTIC), ("vvv", TRACE)):
            monkeypatch.delenv("COMIC_DL_TRACE_HTTP", raising=False)
            set_verbosity(0)
            set_verbosity(level)
            stage_line("Fetching chapter...")
            http_event("GET", "https://s/img.webp", status=200, level=VERBOSE)
            trace("dispatch: fsicomics.com → FsicomixScraper")
            capsys.readouterr()
            captured[name] = capsys.readouterr().err
        return captured

    def test_higher_levels_are_supersets(self, levels):
        assert levels["vvv"].count("\n") >= levels["vv"].count("\n")
        assert levels["vv"].count("\n") >= levels["v"].count("\n")


class TestErrorBlock:
    def test_error_block_indents_details(self, capsys):
        from comic_dl.ui import print_error_block

        print_error_block("58 pages failed verification.", ["page_0001.webp: missing"])
        err = capsys.readouterr().err
        assert "58 pages failed verification." in err
        assert "page_0001.webp: missing" in err
        assert "page_0001" in err and "page_0001".find("✘") == -1

    def test_partial_block_has_single_conclusion(self, capsys):
        from comic_dl.ui import print_partial_block

        print_partial_block("https://s/ch2", missing=58, total=124, output_dir="/out")
        err = capsys.readouterr().err
        assert "partial download: 58 of 124 pages missing" in err
        assert "rerun to resume" in err

    def test_partial_block_accepts_path_output_dir(self, capsys):
        # The batch runner passes args.output (a Path); rich.markup.escape
        # rejects Path, which used to crash the partial recap mid-print.
        from pathlib import Path

        from comic_dl.ui import print_partial_block

        print_partial_block(
            "https://s/ch2", missing=58, total=124, output_dir=Path("/out")
        )
        err = capsys.readouterr().err
        assert "partial download: 58 of 124 pages missing" in err
        assert "rerun to resume" in err

    def test_interrupt_merged_single_message(self, capsys):
        from comic_dl.ui import print_interrupt

        print_interrupt("Downloading 32/124", partial=True)
        err = capsys.readouterr().err
        # The two old lines collapse into one block; no bare "Interrupted." spam.
        assert "Interrupted. (Downloading 32/124)" in err
        assert "rerun to resume" in err

    def test_interrupt_no_partial_omits_resume_hint(self, capsys):
        from comic_dl.ui import print_interrupt

        print_interrupt("Downloading 32/124", partial=False)
        err = capsys.readouterr().err
        assert "Interrupted. (Downloading 32/124)" in err
        assert "rerun to resume" not in err
        assert "Partial save kept" not in err

    def test_interrupt_with_resume_cmd_shows_command(self, capsys):
        from comic_dl.ui import print_interrupt

        print_interrupt(
            "Downloading 32/124",
            partial=True,
            resume_cmd="comic-dl -u https://example.com/g/1/ -o /tmp/out",
        )
        err = capsys.readouterr().err
        assert "Interrupted. (Downloading 32/124)" in err
        assert "rerun to resume" in err
        assert "https://example.com/g/1/" in err
        assert "/tmp/out" in err
        assert "<url>" not in err

    def test_interrupt_without_resume_cmd_falls_back(self, capsys):
        from comic_dl.ui import print_interrupt

        print_interrupt("Downloading 32/124", partial=True)
        err = capsys.readouterr().err
        assert "rerun to resume" in err
        assert "comic-dl -u <url> -o <dir>" in err


class TestDebugFile:
    @pytest.fixture(autouse=True)
    def _reset_verbosity(self):
        set_verbosity(0)
        yield
        set_verbosity(0)
        set_debug_file(None)

    def test_writes_vlog_lines_and_resets(self, tmp_path, monkeypatch):
        from comic_dl.ui import set_debug_file, set_verbosity, vlog

        path = str(tmp_path / "trace.log")
        set_debug_file(path)
        set_verbosity(TRACE)
        vlog(TRACE, "hello trace", tag="http")
        vlog(TRACE, "plain line")
        set_debug_file(None)
        content = (tmp_path / "trace.log").read_text()
        assert "[http] hello trace" in content
        assert "plain line" in content

    def test_none_resets_to_console(self, capsys):
        from comic_dl.ui import set_debug_file, set_verbosity, vlog

        set_debug_file(None)
        set_verbosity(VERBOSE)
        vlog(VERBOSE, "back on console")
        assert "back on console" in capsys.readouterr().err

    def test_creates_parent_dirs_and_owner_only_perms(self, tmp_path):
        import os
        from stat import S_IMODE

        from comic_dl.ui import set_debug_file

        path = tmp_path / "nested" / "logs" / "trace.log"
        set_debug_file(str(path))
        set_debug_file(None)
        assert path.exists()
        # The file must not be group/other readable (0600).
        assert S_IMODE(os.stat(path).st_mode) & 0o077 == 0


class TestSinkDurability:
    """_RowSink result lines must coordinate with the Live region: the
    retiring row is painted first, and the durable line goes through the
    Live's console so scrollback never collects stale spinner frames."""

    @pytest.mark.asyncio
    async def test_succeed_prints_through_live_console(self, monkeypatch):
        import comic_dl.ui as ui_mod

        printed = []
        act = ui_mod.Activity(quiet=False)
        async with act:
            assert ui_mod._ACTIVE_LIVE is not None
            live_console = ui_mod._ACTIVE_LIVE.console
            monkeypatch.setattr(
                live_console, "print", lambda x: printed.append(str(x))
            )
            sink = act.row("main")
            await sink.succeed("Saved: X.cbz (1.0 MB)")
        assert any("Saved: X.cbz" in line for line in printed)

    @pytest.mark.asyncio
    async def test_fail_prints_through_live_console(self, monkeypatch):
        import comic_dl.ui as ui_mod

        printed = []
        act = ui_mod.Activity(quiet=False)
        async with act:
            live_console = ui_mod._ACTIVE_LIVE.console
            monkeypatch.setattr(
                live_console, "print", lambda x: printed.append(str(x))
            )
            sink = act.row("main")
            await sink.fail("No valid pages downloaded (3 failed).")
        assert any("No valid pages" in line for line in printed)

    @pytest.mark.asyncio
    async def test_succeed_without_live_uses_plain_console(self, monkeypatch):
        import comic_dl.ui as ui_mod

        printed = []
        monkeypatch.setattr(
            ui_mod.console, "print", lambda x: printed.append(str(x))
        )
        act = ui_mod.Activity(quiet=True)
        async with act:
            assert ui_mod._ACTIVE_LIVE is None
            sink = act.row("main")
            await sink.succeed("Saved: X.cbz")
        assert any("Saved: X.cbz" in line for line in printed)

    def test_running_row_never_renders_internal_key(self):
        import comic_dl.ui as ui_mod

        state = ui_mod.RowState(key="main", stage="Creating CBZ archive...")
        group = ui_mod._running_row_renderable(state, None, 0)
        rendered = "".join(line.plain for line in group.renderables)
        assert "main" not in rendered
        assert "Creating CBZ archive..." in rendered

    def test_single_chapter_overall_uses_page_fraction(self):
        import comic_dl.ui as ui_mod

        act = ui_mod.Activity(quiet=False)  # quiet=True drops progress state
        act.begin_batch(1)
        act.mark_running("ch", stage="Downloading images...")
        act.show_progress("ch", total=84)
        act.update_progress("ch", 61)

        line = act._overall_renderable().plain
        assert "61/84" in line
        assert "73%" in line
        assert "0/1" not in line

    def test_multi_chapter_overall_keeps_chapter_fraction(self):
        import comic_dl.ui as ui_mod

        act = ui_mod.Activity(quiet=False)
        act.begin_batch(10)
        act.mark_running("ch1", stage="Downloading images...")
        act.show_progress("ch1", total=50)
        act.update_progress("ch1", 49)

        line = act._overall_renderable().plain
        assert "0/10" in line
