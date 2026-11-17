# Windows downloads

Both installers install the same complete CaptionFlow app and use the same
Windows **Installed apps** uninstall entry.

- **Normal — `CaptionFlow-Setup.exe`:** a small online installer. It downloads the
  version-pinned all-in-one installer over HTTPS, checks its exact size and
  SHA-256, then installs it. Internet access is required during installation.
  This reduces the initial download size, not the final installation size.
- **All-in-one — `CaptionFlow-Setup-All-in-One.exe`:** includes the application,
  embedded Python, Qt, CPU-only PyTorch/Phonon, and the pinned Nemotron/llama.cpp
  CPU and Vulkan runtime archives. No additional app code downloads are needed.

Speech/translation weights are selected separately in the app. Supply the pinned
weights offline or authorize model downloads. All-in-one means bundled code and
runtimes; it does not include every model or graphics-vendor drivers.

Both installers install per user without requesting administrator access. The
normal installer runs its verified payload from a temporary directory; it does
not leave a second application or uninstaller. Uninstall removes the application,
shortcuts and registration while preserving user models and preferences.

## Packaging

Build/sign the all-in-one installer first using `packaging/windows.nsi`, then
run `scripts/build-windows-installers.py --all-in-one COMPLETE.exe --output dist`.
Sign the normal installer afterwards. Signing the all-in-one after creating the
normal installer would invalidate its pinned checksum.

The regular Windows CI workflow builds both. Its loopback-only test installer is
an internal fixture and is never published. The Windows download-variants
workflow adds the two variants to an existing release using its unchanged,
previously verified complete installer. It exercises the actual public HTTPS
path before publishing the normal installer.

Acceptance covers identical installed files, both GPUI previews, bundled code
imports and native archive hashes, offline Phonon inference, checksum rejection,
uninstall cleanup and preservation of user data.
