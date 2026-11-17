# CaptionFlow

Private live captions and translation, with a GPUI interface.

**[Windows x64](https://github.com/HGskrd/CaptionFlow/releases/latest/download/CaptionFlow-Setup.exe)** · **[Mac Apple Silicon](https://github.com/HGskrd/CaptionFlow/releases/latest/download/CaptionFlow-macOS-arm64.dmg)** · **[Mac Intel](https://github.com/HGskrd/CaptionFlow/releases/latest/download/CaptionFlow-macOS-x86_64.dmg)** · **[Linux x86_64 preview](https://github.com/HGskrd/CaptionFlow/releases/latest/download/CaptionFlow-Linux-x86_64.tar.gz)**

Windows: run the installer. Mac: open the DMG and drag CaptionFlow to Applications. Python and inference runtimes are included. Apple Silicon requires macOS 26+; Intel requires macOS 14+. Models download during setup when online participation is enabled.

Linux (Arch/EndeavourOS): download the **[PKGBUILD](https://github.com/HGskrd/CaptionFlow/releases/latest/download/PKGBUILD)** and run `makepkg -si`, or install the prebuilt `.pkg.tar.zst` using `pacman -U`. Launch `captionflow`. See [Linux installation](docs/linux.md). Linux updates install manually; physical audio/GPU validation remains pending.

Choose your audio source, spoken language, and any number of translation targets in the inspector. Each target has its own caption lane. The inspector starts hidden; fullscreen shows only captions. Stop interrupts processing, and model loading shows its current stage and elapsed time.

Updates are managed inside the app: download the changed app, worker, or runtime components, then **Restart to update**. Unchanged runtimes and cached models are reused. Signed manifests and checksums are verified before replacing the installation; the previous installation is retained for recovery. Update checks require online participation.

Windows publisher signing is not configured yet, so SmartScreen may show **unknown publisher**. Mac builds use a persistent local signature and may require **System Settings → Privacy & Security → Open Anyway** on first launch.

[Releases](https://github.com/HGskrd/CaptionFlow/releases) · [Build and release instructions](docs/releases.md) · [Engine and benchmark notes](docs/windows-and-benchmarks.md)
