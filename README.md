# CaptionFlow

Local live captions and translation.

**[Download for Windows](https://github.com/HGskrd/CaptionFlow/releases/latest/download/CaptionFlow-Setup.exe)**

Run the installer, then open CaptionFlow from the Start menu. No Python or CUDA installation needed. First setup downloads the models (~2 GB).

**macOS SwiftUI:** [Apple Silicon (arm64)](https://github.com/HGskrd/CaptionFlow/releases/download/macos-v0.4.5/CaptionFlow-macOS-arm64-0.4.5.dmg) · [Intel (x86_64)](https://github.com/HGskrd/CaptionFlow/releases/download/macos-v0.4.5/CaptionFlow-macOS-x86_64-0.4.5.dmg)

Open the DMG, drag **CaptionFlow** to **Applications**, then open it from Applications. Python and runtime dependencies are included; no terminal or Python installation is needed. Choose your languages and start listening. Missing models download on first use when online participation is enabled. Apple Silicon requires macOS 26+ and uses MLX/Metal; Intel requires macOS 14+ and uses Nemotron CPU speech and Hy-MT2 GGUF CPU translation after completed phrases. Intel was tested under Rosetta; physical Intel performance is unverified. These builds are locally signed, not Apple-notarized; macOS may require **System Settings → Privacy & Security → Open Anyway** for the first launch.

Speech: **Nemotron** (streaming) or **Phonon 2.5** (compact, CPU). Mac also offers **Parakeet Redux**.

Choose **Microphone + call audio** to caption both sides through one model.

Display: **Rolling lines**, **Phrase blocks**, or **Transcript**. Choose 2–4 lines, optional clearing after silence, and gentle animation. Scrollback stays in memory. With both panes in Transcript, scrolling either keeps matching passages together; **Back to live** resumes both.

To benchmark: **Start / resume benchmark** → **Until I stop** or **Quick · 5 minutes**. Progress is saved automatically.

[Releases](https://github.com/HGskrd/CaptionFlow/releases) · [Build and technical notes](docs/windows-and-benchmarks.md)

Build both self-contained DMGs with `./scripts/release-macos.sh`. The packaging Python needs `dmgbuild` and `Pillow`; `uv` supplies pinned, relocatable CPython runtimes. Exact dependency constraints live in `scripts/macos-requirements-{arm64,x86_64}.txt`. An existing signing identity is required. `ARCH=arm64` or `ARCH=x86_64 ./scripts/build-app.sh` builds the development shell. `SWIFT`, `PYTHON`, `PACKAGING_PYTHON`, `SDKROOT`, `DEVELOPER_DIR`, `BUILD_DIR`, and `OUTPUT_DIR` can select installed tools and output locations.
