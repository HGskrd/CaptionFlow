# CaptionFlow Linux x86_64 preview — Arch and EndeavourOS

The Linux release uses the same GPUI interface and v0.4.6 speech/translation
engine as Windows and macOS. Nemotron recognition and Hy-MT2 translation support
CPU and Vulkan without CUDA. The optional Phonon backend is not included in this
Linux preview. Microphone, default-output monitor and mixed capture use PulseAudio
or PipeWire-Pulse, with the shared audio mixer and echo cancellation.

Extract `CaptionFlow-Linux-x86_64.tar.gz` to a permanent folder in your home,
open a terminal there, and run:

```sh
cd CaptionFlow
./setup-arch.sh
./start-captionflow.sh
```

Run as your desktop user. Setup uses `sudo pacman -Syu --needed` for system
libraries, installs Python packages in a private user environment and adds
CaptionFlow to the application menu. It preserves an existing PulseAudio or
PipeWire-Pulse setup; otherwise it installs PipeWire-Pulse and WirePlumber.
Log out and in if audio services were newly installed. Install your GPU vendor's
Vulkan driver for GPU mode; `vulkaninfo --summary` should list the GPU. CPU
inference is selectable explicitly. GPUI itself requires a working Vulkan renderer;
software Vulkan such as `vulkan-swrast` can render the interface without a GPU.

The archive includes matching Python wheels and both native CPU/Vulkan runtimes.
With system libraries already installed, setup can run without networking:

```sh
./setup-arch.sh --skip-system-packages --offline
```

Choose online participation in the app to authorize model downloads and opt-in
diagnostics. Declining keeps app networking disabled. No audio or caption text is
uploaded. Select the compute backend and choose model setup, then start listening.
For offline operation, supply the pinned Nemotron Q8_0 and Hy-MT2 Q4_K_M GGUF
files in `${XDG_DATA_HOME:-~/.local/share}/captionflow/models` before model setup.
All model/runtime files are checksummed.

Data, models, preferences, logs and the private Python environment live under
`${XDG_DATA_HOME:-~/.local/share}/captionflow`, independently of the app folder.
Keep the extracted folder in place for the application-menu entry; rerun setup
after moving it or upgrading the system Python minor version. Startup errors are
in `launcher.log` in that data directory. System audio captures the default output
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
