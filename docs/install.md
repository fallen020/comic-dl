# Installation

Download a build from the
[GitHub Releases page](https://github.com/fallen020/comic-dl/releases). It
runs on its own — no Python, no Node.

Building from source is the fallback, and the only option on macOS. It needs
Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).

> [!WARNING]
> `pip install comic-dl` fails: the name on PyPI belongs to an unrelated
> project. Use a release binary or build from source.

## Check your architecture

Filenames encode the CPU, so find yours first:

```powershell
echo $env:PROCESSOR_ARCHITECTURE   # Windows PowerShell, prints AMD64 or ARM64
```

```bash
uname -m                           # Linux / macOS
```

Two answers, and every comic-dl filename is built from one of them:

| What you got | Your CPU | Debian uses | Fedora / Arch use |
| :----------- | :------- | :--------- | :---------------- |
| `x86_64`, `AMD64` | Intel / AMD | `amd64` | `x86_64` |
| `aarch64`, `ARM64` | Apple Silicon, Snapdragon, Raspberry Pi | `arm64` | `aarch64` |

Same chips, different spellings.

## Which file do I need?

Pick your OS. The commands below use `0.0.4`; only the version number changes
between releases.

| OS | x86-64 | ARM64 |
| :-- | :----- | :---- |
| Windows | `comic-dl-0.0.4-windows-amd64.zip` | Not built; the amd64 one runs emulated |
| Debian / Ubuntu | `comic-dl_0.0.4_amd64.deb` | `comic-dl_0.0.4_arm64.deb` |
| Fedora | `comic-dl-0.0.4-1.fc44.x86_64.rpm` | `comic-dl-0.0.4-1.fc44.aarch64.rpm` |
| Arch Linux | `fallen020-comic-dl-0.0.4-1-x86_64.pkg.tar.zst` | Not built |

A bare `comic-dl-0.0.4-windows-amd64.exe` is attached too, if you would rather
not unzip anything.
| macOS | Build from source | Build from source |
| Android | Not supported | Not supported |

Notes that cost people time:

- **Fedora:** keep the `-1.fc44.` build marker exactly as written. RHEL
  compatibility is not guaranteed.
- **macOS:** builds are unsigned and unnotarized, so no binary is published.
  The solver uses the built-in WKWebView and needs nothing extra.
- **Android:** may run under [Termux](https://termux.dev) from source, but the
  webview solver has not been validated there. Build the `.cbz` files on a
  desktop and copy them across.

Artifacts are tested in CI before publication. If one fails to install or run,
report it with the release version and operating system.

## Install by platform

With the file downloaded, install it.

### Windows

Unzip it and run `comic-dl.exe` from a terminal:

```powershell
.\comic-dl.exe self version
```

Use **Windows Terminal** — the CLI output uses Unicode glyphs. In classic
`cmd.exe`, run `chcp 65001` first.

> [!NOTE]
> **Windows on ARM64:** there is no native build; the amd64 binary runs
> emulated. Build from source for native speed.

The webview solver needs the Microsoft Edge WebView2 Runtime. It ships with
current Windows versions; if the solver fails to start, install it from
Microsoft separately.

### Debian / Ubuntu

```bash
sudo apt install ./comic-dl_0.0.4_amd64.deb
```

On ARM64, install the `_arm64.deb` file instead.

### Fedora

```bash
sudo dnf install ./comic-dl-0.0.4-1.fc44.x86_64.rpm
```

Substitute `.aarch64` for `.x86_64` on ARM64. If dependency resolution fails,
build from source.

### Arch Linux

```bash
sudo pacman -U fallen020-comic-dl-0.0.4-1-x86_64.pkg.tar.zst
```

No ARM64 Arch package is provided; on ARM64, build from source.

> [!WARNING]
> The AUR hosts an unrelated package also named `comic-dl`. Installing it
> with `yay` or `paru` swaps this program out for that one. Get the
> `fallen020-comic-dl-*.pkg.tar.zst` file from the release page instead;
> no AUR entry exists under that name, so helpers never touch it.

If you installed the old `comic-dl` package (0.0.4 or earlier), switch to
the new name:

```bash
sudo pacman -Rns comic-dl   # remove the old package name
sudo pacman -U fallen020-comic-dl-0.0.4-1-x86_64.pkg.tar.zst
```

Your config, cookies, and library database are untouched.

### From source

Needs Python 3.11+ and `uv` on your `PATH`:

```bash
python3 --version
uv --version
```

```bash
git clone https://github.com/fallen020/comic-dl
cd comic-dl
uv sync
```

`uv sync` creates `.venv/` and installs the project with its dependencies. Run
it with `uv run` — the same command works everywhere, Windows PowerShell
included:

```bash
uv run comic-dl self version
```

For the development environment, with test and lint tooling, use
`uv sync --extra dev --locked` instead.

## Webview solver libraries (Linux)

The Cloudflare challenge solver ships with the application, and the Debian,
Fedora, and Arch packages declare its GTK and WebKit dependencies — installing
comic-dl pulls the whole stack in.

A source checkout needs those libraries separately, because the pywebview wheel
cannot ship them:

```bash
# Debian / Ubuntu
sudo apt install python3-gi python3-gi-cairo gir1.2-webkit2-4.1

# Fedora
sudo dnf install python3-gobject webkit2gtk4.1

# Arch
sudo pacman -S python-gobject webkit2gtk-4.1
```

On headless Linux, run under `xvfb-run` or pass `--solver impersonation` to
skip the webview entirely.

## Verify

Check that it runs before your first download:

```bash
comic-dl self version          # binary install
uv run comic-dl self version   # source checkout
```

### Checksums

Every release attaches a `SHA256SUMS` file. After downloading the package,
fetch it from the same release and check the download against it:

```bash
curl -LO https://github.com/fallen020/comic-dl/releases/download/v0.0.4/SHA256SUMS
sha256sum -c SHA256SUMS --ignore-missing
```

`OK` means the file matches the published checksum; `--ignore-missing` skips
the release files you did not download.

## Shell completions

Completions exist for bash, zsh, and fish. Binary installs put `comic-dl` on
`PATH`; from a source checkout, prefix with `uv run`:

| Shell | Binary install | Source checkout |
| :----- | :------------- | :-------------- |
| bash | `source <(comic-dl completion bash)` | `source <(uv run comic-dl completion bash)` |
| zsh | `eval "$(comic-dl completion zsh)"` | `eval "$(uv run comic-dl completion zsh)"` |
| fish | `comic-dl completion fish \| source` | `uv run comic-dl completion fish \| source` |

To load completions on every new shell, write the command to your shell's
startup file — `~/.bashrc`, `~/.zshrc`, or `~/.config/fish/config.fish`.

## Update

`comic-dl self update` works out how comic-dl was installed and updates it
through whichever tool owns that install. It never downgrades, and nothing
changes until you confirm:

```bash
comic-dl self update
```

- `--check` reports the installed and latest versions and changes nothing.
- `-y` / `--yes` skips the confirmation. Without a TTY and without `--yes`, it
  refuses rather than running unattended.
- `--channel beta` opts into pre-releases.

What it does depends on the install:

| Install | Update |
| :------ | :----- |
| Debian/Ubuntu | Downloads the `.deb`, then runs `sudo apt install --yes` on it |
| Fedora | Downloads the `.rpm`, then runs `sudo dnf install --yes` on it |
| Arch | Downloads the `.pkg.tar.zst`, then runs `sudo pacman -U --noconfirm` on it |
| `uv tool` | Runs `uv tool upgrade comic-dl` |
| Source checkout | Prints `git pull` and `uv sync`; never modifies the checkout |
| Windows `.zip` | Prints the Releases page; does not replace itself |

### Can I just install the newer package over the old one?

Yes — on Linux that is what `self update` does anyway, since package managers
upgrade in place. If you already downloaded the newer file, install it the same
way you installed the first one:

```bash
# Debian / Ubuntu
sudo apt install ./comic-dl_0.0.5_amd64.deb

# Fedora
sudo dnf install ./comic-dl-0.0.5-1.fc44.x86_64.rpm

# Arch
sudo pacman -U fallen020-comic-dl-0.0.5-1-x86_64.pkg.tar.zst
```

Swap in the version you downloaded. No uninstall first — the package manager
upgrades over the existing install, and your config, cookies, and library
database are left alone.

See [Version and Updates](usage/self-update.md) for more detail.

## Uninstall

Two steps, and only the first removes the program. **Your downloads are never
touched.**

### 1. Remove the program

```bash
# Windows: delete the folder you unzipped comic-dl into

# Debian / Ubuntu
sudo apt remove comic-dl

# Fedora
sudo dnf remove comic-dl

# Arch
sudo pacman -Rns fallen020-comic-dl

# From source
rm -rf comic-dl
```

### 2. Optionally remove the data

Skip this if you plan to reinstall — keeping the directories means
`comic-dl library list` still knows your series when you come back. comic-dl is
already gone at this point, so this is plain file removal. Each platform's
commands remove the config, the cookie jar, the scrape cache, and the library
database.

```bash
# Linux
rm -rf ~/.config/comic-dl         # config.toml + cookies.db
rm -rf ~/.cache/comic-dl          # scrape cache
rm -rf ~/.local/share/comic-dl    # library.db
```

```bash
# macOS
rm -rf ~/Library/Application\ Support/comic-dl   # config.toml + cookies.db + library.db
rm -rf ~/Library/Caches/comic-dl                 # scrape cache
```

```powershell
# Windows
rmdir /s /q "%LOCALAPPDATA%\comic-dl"   # config.toml + cookies.db + library.db + cache
```

Your downloaded `.cbz` archives live wherever you told comic-dl to put them —
nothing here removes those.

To clear just the cookies and cache without deleting anything, run
`comic-dl cookie clear` and `comic-dl cache clear` *before* removing the
program. Exact paths, including the `$COMIC_DL_CONFIG` and `$COMIC_DL_DATA_DIR`
overrides: [Privacy](privacy.md).
