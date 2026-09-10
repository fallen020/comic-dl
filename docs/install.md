# Installation

Two ways to install:

- **Prebuilt binary** from the
  [GitHub Releases page](https://github.com/fallen020/comic-dl/releases) —
  no Python needed.
- **From source** with `git clone` + `uv sync` — needs Python 3.11+ and
  [uv](https://docs.astral.sh/uv/getting-started/installation/).

`pip install comic-dl` does not work: the package is not published on PyPI
(the name is held by an unrelated project). Use a release binary or build
from source.

## Check your architecture

Release files differ by CPU architecture. Find yours first:

```bash
uname -m   # Linux / macOS
```

```powershell
echo $env:PROCESSOR_ARCHITECTURE   # Windows PowerShell, prints AMD64 or ARM64
```

| Output | Architecture | Filename hint |
| :----- | :----------- | :------------ |
| `x86_64`, `AMD64` | x86-64 (Intel/AMD) | `amd64`, `x86_64` |
| `aarch64`, `ARM64` | ARM64 (Apple Silicon, Snapdragon, Raspberry Pi) | `arm64`, `aarch64` |

(Debian packages use `amd64`/`arm64`; Fedora and Arch use
`x86_64`/`aarch64` — same chips, different names.)

## Which file do I need?

| Your machine | What to get |
| :----------- | :---------- |
| Windows x86-64 | `comic-dl-<ver>-windows-amd64.zip` |
| Debian / Ubuntu x86-64 | `comic-dl_<ver>_amd64.deb` |
| Debian / Ubuntu ARM64 | `comic-dl_<ver>_arm64.deb` |
| Fedora x86-64 / ARM64 | `comic-dl-<ver>-1.fcNN.x86_64.rpm` / `...aarch64.rpm` |
| Arch Linux x86-64 | `comic-dl-<ver>-1-x86_64.pkg.tar.zst` |
| macOS, Android, anything else | Build from source |

Release artifacts are tested in CI before publication. If one fails to
install or run, please report it with the release version and operating
system.

## Windows

Download the `comic-dl-<ver>-windows-amd64.zip` from the release page,
unzip it, and run `comic-dl.exe` from a terminal:

```powershell
.\comic-dl.exe --version
```

Use **Windows Terminal** — the CLI output uses Unicode glyphs. In classic
`cmd.exe`, run `chcp 65001` first.

> [!NOTE]
> **Windows on ARM64:** there is no native ARM64 build; the amd64 binary
> runs via emulation. For native speed, build from source instead.

The webview solver needs the Microsoft Edge WebView2 Runtime. It ships
with current Windows versions; if the solver fails to start, install the
runtime from Microsoft separately.

## Linux

Pick the file matching your distro and architecture from the
[Releases page](https://github.com/fallen020/comic-dl/releases), replacing
`<ver>` below with the release version. Filenames use the bare version
(`0.0.1`); the download path uses the tag (`v0.0.1`).

### Debian / Ubuntu

```bash
curl -LO https://github.com/fallen020/comic-dl/releases/download/v<ver>/comic-dl_<ver>_amd64.deb
sudo apt install ./comic-dl_<ver>_amd64.deb
```

On ARM64, use the `_arm64.deb` file instead.

### Fedora

```bash
curl -LO https://github.com/fallen020/comic-dl/releases/download/v<ver>/comic-dl-<ver>-1.fcNN.x86_64.rpm
sudo dnf install ./comic-dl-<ver>-1.fcNN.x86_64.rpm
```

Use the exact filename from the release page, including the Fedora build
marker (`-1.fcNN.`), and substitute `.aarch64` for `.x86_64` on ARM64.
RHEL compatibility is not guaranteed — if dependency resolution fails,
build from source.

### Arch Linux

```bash
curl -LO https://github.com/fallen020/comic-dl/releases/download/v<ver>/comic-dl-<ver>-1-x86_64.pkg.tar.zst
sudo pacman -U comic-dl-<ver>-1-x86_64.pkg.tar.zst
```

No ARM64 Arch package is provided; on ARM64, build from source.

## From source

Requires Python 3.11+ and `uv` on your `PATH`:

```bash
python3 --version
uv --version
```

```bash
git clone https://github.com/fallen020/comic-dl
cd comic-dl
uv sync
```

`uv sync` creates `.venv/` and installs the project with its dependencies.
Run it with `uv run`, which works on every platform including Windows
PowerShell:

```bash
uv run comic-dl --version
```

For the development environment (extra test/lint tooling), use
`uv sync --extra dev --locked` instead.

**macOS:** no binary is distributed (signing and notarization are not set
up), so build from source. The solver uses the built-in WKWebView; nothing
extra to install.

**Android:** not supported. The CLI may run under
[Termux](https://termux.dev) from source, but this is untested and the
webview solver has not been validated there. The practical path is to
create `.cbz` archives on a desktop machine and copy them to your phone.

## Webview solver (Cloudflare)

The Cloudflare challenge solver ships with the application, but on Linux it
needs system libraries that the packages do not bundle. Install them from
your distro's package manager:

```bash
# Debian / Ubuntu
sudo apt install python3-gi python3-gi-cairo gir1.2-webkit2-4.1 gir1.2-gtk-4.0

# Fedora
sudo dnf install python3-gobject webkit2gtk4.1

# Arch
sudo pacman -S python-gobject webkit2gtk
```

On headless Linux, run under `xvfb-run` or pass `--solver impersonation`
to skip the webview entirely.

## Shell completions

Completions exist for bash, zsh, and fish. Binary installs put `comic-dl`
on `PATH`; from a source checkout, prefix with `uv run`:

```bash
eval "$(comic-dl completion zsh)"          # zsh (binary install)
source <(comic-dl completion bash)         # bash (binary install)
comic-dl completion fish | source          # fish (binary install)
eval "$(uv run comic-dl completion zsh)"   # zsh (source checkout)
```

## Update and uninstall

- **Windows:** download the newer `.zip` and replace the old folder.
- **Debian / Ubuntu:** install the newer `.deb` the same way; remove with
  `sudo apt remove comic-dl`.
- **Fedora:** install the newer `.rpm` the same way; remove with
  `sudo dnf remove comic-dl`.
- **Arch:** install the newer package the same way; remove with
  `sudo pacman -Rns comic-dl`.
- **From source:** `git pull` and `uv sync` again.

Uninstalling removes the program only; your configuration and downloaded
files stay in place.

## Verify

```bash
comic-dl --version          # binary install
uv run comic-dl --version   # source checkout
```
