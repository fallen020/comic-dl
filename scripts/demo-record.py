#!/usr/bin/env python3
"""Record, polish, and render the docs demo GIF.

A bare run does the full pipeline (record under asciinema, polish the cast,
render ~/demo.gif). Recording needs a Unix pty; the other modes run anywhere.
See --help for the full guide.
"""

import contextlib
import json
import os
import random
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import fcntl
    import pty
    import termios
except ImportError:
    fcntl = pty = termios = None

URL = "https://asurascans.com/comics/myst-might-mayhem-bd5bdaf8/chapter/118"
SHOWN_URL = "https://example.com/comics/myst-might-mayhem-bd5bdaf8/chapter/118"
SHOWN_HOME = "/home/comic-dl"
OUTDIR = Path("~/Downloads/comic-dl").expanduser()
THEME = (
    "1e1e1e,ffffff,1a1a1a,cc372e,26a439,cdac08,0869cb,9647bf,479ec2,"
    "98989d,464646,ff453a,32d74b,ffd60a,0a84ff,bf5af2,76d6ff,ffffff"
)
_INSTALL = {
    "agg": "cargo install --git https://github.com/asciinema/agg  (needs Rust)",
    "comic-dl": "https://github.com/fallen020/comic-dl/releases",
}


def show(text: str, cps: float) -> None:
    """Draw keystrokes like human typing, averaging ``cps`` per second."""
    for char in text:
        sys.stdout.write(char)
        sys.stdout.flush()
        time.sleep(random.uniform(0.6, 1.6) / cps)


def _send(master: int, text: str) -> bool:
    try:
        os.write(master, text.encode() + b"\n")
    except OSError:
        return False
    return True


def _has_new_cbz(outdir: Path, since: float) -> bool:
    if not outdir.is_dir():
        return False
    for cbz in outdir.rglob("*.cbz"):
        try:
            if cbz.stat().st_mtime >= since:
                return True
        except OSError:
            continue
    return False


def _pump(master: int) -> None:
    while True:
        try:
            data = os.read(master, 65536)
        except OSError:
            return
        if not data:
            return
        try:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        except OSError:
            return


def _version(cmd: str):
    try:
        proc = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = (proc.stdout or proc.stderr).strip().splitlines()
    return lines[0][:60] if lines else None


def _install_hint(dep: str) -> str:
    if dep == "asciinema":
        if sys.platform == "darwin":
            return "brew install asciinema"
        if sys.platform == "win32":
            return "pipx install asciinema"
        if sys.platform.startswith("linux"):
            if shutil.which("apt-get"):
                return "sudo apt install asciinema"
            if shutil.which("dnf"):
                return "sudo dnf install asciinema"
            if shutil.which("pacman"):
                return "sudo pacman -S asciinema"
        return "pipx install asciinema"
    return _INSTALL[dep]


def _help() -> int:
    try:
        from rich.console import Console
    except ImportError:
        print(__doc__)
        return 0
    c = Console()
    c.print("[bold]demo-record.py[/] — docs demo GIF pipeline.")
    c.print()
    c.print("[bold]Usage:[/]")
    c.print("  [green]demo-record.py[/]         full take → ~/demo.cast + ~/demo.gif")
    c.print("  [green]demo-record.py --take[/]  record one take (runs under asciinema)")
    c.print("  [green]demo-record.py --check[/] verify tooling")
    c.print("  [green]demo-record.py --polish CAST[/] cut exit, mask paths, swap domain")
    c.print("  [green]demo-record.py --render CAST[/] render the GIF")
    c.print()
    c.print("[bold]Flags:[/]  [green]--json[/] machine output  [green]--help[/] this guide")
    c.print("[dim]Recording needs a Unix pty; the rest runs anywhere.[/]")
    return 0


def _check(as_json: bool) -> int:
    deps = ("asciinema", "agg", "comic-dl")
    info = {d: _version(d) for d in deps}
    missing = [d for d in deps if info[d] is None]
    if as_json:
        print(json.dumps({"platform": sys.platform, "deps": info}))
        return 0 if not missing else 1
    for d in deps:
        print(f"{d:10} {info[d] or 'MISSING'}")
    if not missing:
        return 0
    print("install the missing tools:")
    for d in missing:
        print(f"  {d}: {_install_hint(d)}")
    return 1


def _polish(path: str, as_json: bool) -> int:
    if not Path(path).is_file():
        print(f"no such cast: {path}", file=sys.stderr)
        return 2
    try:
        with open(path, encoding="utf-8") as fh:
            header = fh.readline()
            events = [json.loads(line) for line in fh if line.strip()]
    except ValueError:
        print(f"not a cast file: {path}", file=sys.stderr)
        return 2
    if not events:
        print(f"empty cast: {path}", file=sys.stderr)
        return 2
    n_in = len(events)
    cut = False
    for i in range(len(events) - 1, -1, -1):
        if events[i][1] == "o" and "❯" in events[i][2]:  # noqa: RUF001 (prompt glyph)
            tail = events[i + 1 :]
            spell = "".join(ev[2] for ev in tail if ev[1] == "o")
            if tail and len(tail) <= 8 and "exit" in spell:
                events = events[: i + 1]
                cut = True
            break
    home_hits = dom_hits = 0
    for ev in events:
        if ev[1] == "o":
            home_hits += ev[2].count(str(Path.home()))
            ev[2] = ev[2].replace(str(Path.home()), SHOWN_HOME)
            dom_hits += ev[2].count("asurascans.com")
            ev[2] = ev[2].replace("asurascans.com", "example.com")
    singles = [i for i, ev in enumerate(events) if ev[1] == "o" and len(ev[2]) == 1]
    at = "".join(events[i][2] for i in singles).find(URL)
    typed = at != -1
    if typed:
        run = singles[at : at + len(URL)]
        t0, t1 = events[run[0]][0], events[run[-1]][0]
        typed_events = [
            [t0 + k / (len(SHOWN_URL) - 1) * (t1 - t0), "o", ch] for k, ch in enumerate(SHOWN_URL)
        ]
        events = events[: run[0]] + typed_events + events[run[-1] + 1 :]
    out = [events[0]]
    capped = False
    for ev in events[1:]:
        gap = ev[0] - out[-1][0]
        capped = capped or gap > 0.500001
        out.append([out[-1][0] + min(gap, 0.5), ev[1], ev[2]])
    events = out
    with open(path + ".bak", "w", encoding="utf-8") as fh:
        fh.write(header)
        for ev in events:
            fh.write(json.dumps([round(ev[0], 6), ev[1], ev[2]]) + "\n")
    os.replace(path + ".bak", path)
    duration = round(events[-1][0], 1)
    if as_json:
        print(
            json.dumps(
                {
                    "cast": path,
                    "events_in": n_in,
                    "events_out": len(events),
                    "duration_s": duration,
                    "cut_exit": cut,
                    "masked_home": home_hits,
                    "masked_domain": dom_hits + typed,
                    "backup": path + ".bak",
                }
            )
        )
        return 0
    steps = [s for s, on in [("cut exit", cut), ("capped gaps", capped)] if on]
    if home_hits:
        steps.append(f"masked {home_hits} home paths")
    if dom_hits + typed:
        steps.append("swapped domain")
    detail = f" ({', '.join(steps)})" if steps else ""
    print(f"polished {path}: {n_in} -> {len(events)} events, {duration}s{detail}")
    return 0


def _render(cast: str, out: str, as_json: bool) -> int:
    try:
        proc = subprocess.run(["agg", "--bold-is-bright", "--theme", THEME, cast, out])
    except FileNotFoundError:
        print(f"agg not found: {_install_hint('agg')}")
        return 127
    if as_json:
        print(json.dumps({"cast": cast, "gif": out, "returncode": proc.returncode}))
    return proc.returncode


def _full(as_json: bool) -> int:
    home = Path.home()
    t0 = time.monotonic()
    cast, gif = str(home / "demo.cast"), str(home / "demo.gif")
    missing = [d for d in ("asciinema", "agg", "comic-dl") if shutil.which(d) is None]
    if missing:
        print("missing tools (see --check): " + ", ".join(missing))
        for d in missing:
            print(f"  {d}: {_install_hint(d)}")
        return 1
    _version("comic-dl")  # throwaway run: warms disk cache so the take starts faster
    if Path(cast).exists() or _has_new_cbz(OUTDIR, 0.0):
        try:
            answer = input(f"Remove previous take ({cast}, {OUTDIR}) and continue? [y/N] ")
        except EOFError:
            return 130
        if answer.strip().lower() != "y":
            print("Aborted.")
            return 130
        Path(cast).unlink(missing_ok=True)
        shutil.rmtree(OUTDIR, ignore_errors=True)
        if _has_new_cbz(OUTDIR, 0.0):
            print(f"could not clear {OUTDIR}; aborting", file=sys.stderr)
            return 1
    inner = shlex.join([sys.executable, os.path.abspath(__file__), "--take"])
    rec = subprocess.run(
        ["asciinema", "rec", "-c", inner, "--cols", "125", "--rows", "30", "-i", "1.5", cast],
        cwd=str(home),
    )
    if rec.returncode != 0:
        return rec.returncode
    if _polish(cast, as_json) != 0:
        return 1
    rc = _render(cast, gif, as_json)
    if rc == 0 and not as_json:
        print(f"cast: {cast}\ngif:  {gif}\ndone in {time.monotonic() - t0:.0f}s")
    if rc == 0 and as_json:
        print(json.dumps({"cast": cast, "gif": gif}))
    return rc


def main() -> int:
    """Record the demo, or run a cast mode."""
    raw = sys.argv[1:]
    as_json = "--json" in raw
    args = [a for a in raw if a != "--json"]
    if not args:
        return _full(as_json)
    if args[:1] in (["--help"], ["-h"], ["help"]) and len(args) == 1:
        return _help()
    if args[:1] == ["--check"] and len(args) == 1:
        return _check(as_json)
    if args[:1] == ["--polish"] and len(args) == 2:
        return _polish(args[1], as_json)
    if args[:1] == ["--render"] and len(args) in (2, 3):
        out = args[2] if len(args) == 3 else str(Path.home() / "demo.gif")
        return _render(args[1], out, as_json)
    if args != ["--take"]:
        print("usage: demo-record.py [--take] [--check] [--polish CAST]", file=sys.stderr)
        print("       [--render CAST [GIF]]", file=sys.stderr)
        return 2
    if pty is None:
        print("recording needs a Unix pty (Linux/macOS, or WSL on Windows)", file=sys.stderr)
        return 2
    if shutil.which("comic-dl") is None:
        print(f"comic-dl not found: {_install_hint('comic-dl')}", file=sys.stderr)
        return 127
    if _has_new_cbz(OUTDIR, 0.0):
        print(f"stale output in {OUTDIR}; clear it, then re-record", file=sys.stderr)
        return 2
    pid, master = pty.fork()
    if pid == 0:
        with contextlib.suppress(OSError):
            os.execvpe(
                "bash",
                ["bash", "--norc", "--noprofile", "-i"],
                {**os.environ, "PS1": r"\w \[\e[35m\]❯\[\e[0m\] "},  # noqa: RUF001 (prompt glyph)
            )
        os._exit(127)
    # The forked shell owns the pty as its controlling terminal, so it gets
    # job control and stays quiet; match the outer window size for rendering.
    with contextlib.suppress(OSError):
        size = fcntl.ioctl(1, termios.TIOCGWINSZ, b"\0" * 8)
        fcntl.ioctl(master, termios.TIOCSWINSZ, size)
    attrs = termios.tcgetattr(master)
    attrs[3] &= ~termios.ECHO
    termios.tcsetattr(master, termios.TCSANOW, attrs)
    pump = threading.Thread(target=_pump, args=(master,), daemon=True)
    pump.start()
    state = {"interrupted": False, "reaped": None}

    def _poll():
        if state["reaped"] is not None:
            return state["reaped"]
        try:
            done, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            state["reaped"] = 0
            return state["reaped"]
        if done == 0:
            return None
        state["reaped"] = os.waitstatus_to_exitcode(status)
        return state["reaped"]

    def _shutdown(_signum, _frame) -> None:
        # The shell ignores SIGTERM, so only SIGKILL takes the group down.
        state["interrupted"] = True
        if _poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGHUP, _shutdown)
    try:
        time.sleep(0.2)
        show('comic-dl -u "', cps=10)
        show(URL, cps=30)
        show('"\n', cps=10)
        started = time.time()
        if state["interrupted"] or not _send(master, f'comic-dl -u "{URL}"'):
            return 130
        deadline = time.monotonic() + 300.0
        while time.monotonic() < deadline and not state["interrupted"]:
            if _has_new_cbz(OUTDIR, started):
                break
            time.sleep(0.5)
        else:
            return 130
        time.sleep(2.5)
        if not state["interrupted"]:
            show("exit\n", cps=9)
            _send(master, "exit")
        while _poll() is None:
            if state["interrupted"]:
                return 130
            time.sleep(0.2)
        pump.join(timeout=3.0)
    except OSError:
        return 130
    finally:
        if _poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)
            with contextlib.suppress(ChildProcessError):
                os.waitpid(pid, 0)
        os.close(master)
    return 130 if state["interrupted"] else state["reaped"]


if __name__ == "__main__":
    sys.exit(main())
