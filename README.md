# CaptionFlow

Local live captions and translation.

**[Download for Windows](https://github.com/HGskrd/CaptionFlow/releases/latest/download/CaptionFlow-Setup.exe)**

Run the installer, then open CaptionFlow from the Start menu. No Python or CUDA installation needed. First setup downloads the models (~2 GB).

**macOS SwiftUI:** [Apple Silicon (arm64)](https://github.com/HGskrd/CaptionFlow/releases/download/macos-v0.4.4/CaptionFlow-macOS-arm64-0.4.4.zip) · [Intel (x86_64)](https://github.com/HGskrd/CaptionFlow/releases/download/macos-v0.4.4/CaptionFlow-macOS-x86_64-0.4.4.zip)

Extract the ZIP and move **CaptionFlow.app** into Applications. These builds are locally signed, not Apple-notarized; macOS may require **System Settings → Privacy & Security → Open Anyway** for the first launch. Under Models, supply a Python 3.11+ executable matching your Mac's architecture and install the private runtime. Apple Silicon uses MLX/Metal; Intel uses Nemotron CPU speech and Hy-MT2 GGUF CPU translation. CPU translation updates after each completed phrase. The app targets macOS 14+; individual MLX dependencies may require a newer macOS. Intel is cross-built and tested under Rosetta, without a physical Intel Mac performance claim.

Speech: **Nemotron** (streaming) or **Phonon 2.5** (compact, CPU). Mac also offers **Parakeet Redux**.

Choose **Microphone + call audio** to caption both sides through one model.

Display: **Rolling lines**, **Phrase blocks**, or **Transcript**. Choose 2–4 lines, optional clearing after silence, and gentle animation. Scrollback stays in memory. With both panes in Transcript, scrolling either keeps matching passages together; **Back to live** resumes both.

To benchmark: **Start / resume benchmark** → **Until I stop** or **Quick · 5 minutes**. Progress is saved automatically.

[Releases](https://github.com/HGskrd/CaptionFlow/releases) · [Build and technical notes](docs/windows-and-benchmarks.md)

Build both Mac archives locally with `./scripts/release-macos.sh` (requires an existing signing identity). `ARCH=arm64` or `ARCH=x86_64 ./scripts/build-app.sh` builds one architecture. `SWIFT`, `PYTHON`, `SDKROOT`, `DEVELOPER_DIR`, `BUILD_DIR`, and `OUTPUT_DIR` can select installed tools and output locations. For example, this Mac uses the Xcode Swift compiler directly with `DEVELOPER_DIR=/Library/Developer/CommandLineTools` and `SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.2.sdk`.
