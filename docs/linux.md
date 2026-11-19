# CaptionFlow Linux x86_64 preview — Arch and EndeavourOS

The Linux release uses the same GPUI interface and v0.4.6 speech/translation
engine as Windows and macOS. Nemotron recognition and Hy-MT2 translation support
CPU and Vulkan without CUDA. The optional Phonon backend is not included in this
Linux preview. Microphone, default-output monitor and mixed capture use PulseAudio
or PipeWire-Pulse, with the shared audio mixer and echo cancellation.

## Install using pacman

Download **PKGBUILD** from [GitHub Releases](https://github.com/HGskrd/CaptionFlow/releases/latest)
into an empty directory, inspect it, and run as your normal user:

```sh
makepkg -si
captionflow
```

`makepkg` downloads and verifies the pinned release archive and builds
`captionflow-bin`. Dependencies come from Arch's official repositories. Pacman
owns the executable, Python backend, native runtimes, menu entry and icon; there
is no setup script or pip step on first launch. SoundCard is kept privately in
the app directory because it is not available in the official repositories.
The other Python libraries, including Qt, use Arch packages and follow Arch
Python upgrades.

Alternatively, download the prebuilt **captionflow-bin-0.4.6-1-x86_64.pkg.tar.zst**
from the release and install it with:

```sh
sudo pacman -U ./captionflow-bin-0.4.6-1-x86_64.pkg.tar.zst
```

Pacman preserves an existing PulseAudio/PipeWire-Pulse provider and Vulkan
provider. If none is installed, choose the provider appropriate for your system
when pacman prompts. `vulkan-swrast` supplies software rendering for the GPUI
interface on CPU-only machines. Vulkan inference needs a suitable hardware GPU
driver; select CPU explicitly if it is unavailable.

Launch **CaptionFlow** from the menu or run `captionflow`. Remove the application
with `sudo pacman -R captionflow-bin`; model caches and preferences stay in your
user data directory. If you previously ran the portable setup script, remove
its obsolete `~/.local/share/applications/captionflow.desktop` shortcut so the
system package's menu entry is used. The package does not edit your home files.

This recipe is distributed in the repository and GitHub Releases; it has not
been submitted to the AUR.

## Portable alternative

The existing `CaptionFlow-Linux-x86_64.tar.gz` remains available. Extract it to a
permanent folder and run `./setup-arch.sh`, then `./start-captionflow.sh`. This
alternative uses a private Python environment and the bundled wheels. Offline
portable setup is `./setup-arch.sh --skip-system-packages --offline` after system
libraries are installed. Keep the extracted folder in place and rerun portable
setup after moving it or upgrading the Python minor version.

## Develop from source with uv

On Arch or EndeavourOS x86_64, install `uv`, Rust via `rustup`, a C compiler,
and the desktop libraries listed in the [Arch build workflow](../.github/workflows/linux.yml).
Run these commands from the repository root.

```sh
uv sync --locked
uv run --locked python scripts/build-audio.py
uv run --locked python scripts/run-linux.py
```

`uv` creates a checkout-local `.venv` with the locked Python wheels (CPython
3.13 or 3.14). The audio build produces the shared DSP library. The launcher
builds GPUI with the Rust toolchain pinned in `gpui/rust-toolchain.toml` and
runs it against the Python backend in your checkout. Inference runtimes and
models download separately during model setup if you opt in. For offline model
setup from source, also place the pinned native runtime archives for your
backend in the user data directory's `downloads/` subdirectory. To preview
sample captions, append `--smoke-test` to the launcher command.

## Models and data

Choose online participation in the app to authorize model downloads and opt-in
diagnostics. Declining keeps app networking disabled. No audio or caption text is
uploaded. Select the compute backend and choose model setup, then start listening.
For offline operation, supply the pinned Nemotron Q8_0 and Hy-MT2 Q4_K_M GGUF
files in `${XDG_DATA_HOME:-~/.local/share}/captionflow/models` before model setup.
All model/runtime files are checksummed.

Data, models, preferences and logs live under
`${XDG_DATA_HOME:-~/.local/share}/captionflow`, independently of the app folder.
The portable launcher logs startup errors in `launcher.log` in that directory. System audio captures the default output
at session start; restart listening after switching outputs. Keep the machine
awake during Linux benchmarks.

The **Linux Arch preview** GitHub Actions workflow builds GPUI on Arch, tests the
engine and audio DSP, and checks an offline installed archive with a real GPUI
window, caption preview and clean shutdown under Xvfb/software Vulkan. Physical
microphone capture, PipeWire monitor capture, hardware GPU inference and endurance
remain unverified. Linux updates are installed manually from GitHub Releases;
component updates are disabled for this preview.

From a Linux source checkout, build GPUI with Rust 1.98.0 and run
`python scripts/build-linux.py --python-version 3.14` using a private packaging
Python environment. The target Python minor version must match the bundled wheels
for offline setup. See the workflow for the complete build dependencies.

The **Arch pacman package** workflow separately builds this PKGBUILD as a normal
user, installs the resulting package with pacman, checks its files and desktop
entry, launches the installed GPUI app with Arch's Python/Qt packages, and removes
the package while retaining user data. It publishes the verified `.pkg.tar.zst`
and recipe as CI artifacts for the release.
