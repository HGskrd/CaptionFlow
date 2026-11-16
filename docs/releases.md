# Releases

Use one `vX.Y.Z` release containing exactly three user downloads and `SHA256SUMS.txt`. Do not publish platform-only releases or relabel old binaries. Keep notes under 100 words, covering changes and installation requirements; store test details in this repository.

`Sources/CaptionFlow/Resources/worker/release.json` is the version and increasing update sequence shared by the clients and packaging. The Rust package version must match. GPUI is the release frontend on all platforms. `script/build_and_run.sh` builds and launches a development Mac app; `script/test_native.sh` runs the native suite.

## Packaging

1. Commit the candidate and run **Windows installer** from that exact branch in GitHub Actions. It builds GPUI on Windows, assembles the installer on Linux, then installs, launches and tests it on Windows. Use the `CaptionFlow-Windows-verified` artifact only after the whole run succeeds.
2. For both Mac DMGs, run `PACKAGING_PYTHON=.venv/bin/python PYTHON=.venv/bin/python ./scripts/release-macos.sh`. Packaging Python needs Pillow and dmgbuild; Rust 1.98.0, both Mac Rust targets, Xcode Swift tools, and the existing CaptionFlow signing identity are required. Runtime dependencies are pinned by architecture. Test both installed bundles, including the Intel build under Rosetta.
3. Assemble the three installers in one directory. Run `.venv/bin/python scripts/publish-release.py DIRECTORY --notes docs/releases/vX.Y.Z.md` for preflight; add `--publish` only after the install/update acceptance checks pass. Uploads go to a draft and become public only with all three installers and checksums.

## Component updates

Run `scripts/package-update.py --root INSTALLED_TREE --platform PLATFORM --output dist/updates` for `windows-x86_64`, `macos-aarch64` and `macos-x86_64`. Use a Python environment with cryptography. The default private key is `~/.config/captionflow/release-signing.key`; it stays outside Git and off the server.

The packager produces deterministic app, worker, Python and native-runtime archives. On Mac, `DEVELOPER_DIR=/Library/Developer/CommandLineTools .venv/bin/python scripts/test-component-update.py` checks unchanged-runtime reuse, signed installation/relaunch, and tamper rejection with disposable fixtures. It signs the complete target file inventory, sizes, hashes, modes, platform, increasing sequence and expiration. The app compares those files with its installation, downloads only changed components, stages a new verified installation, stops capture and swaps the installation using a separate helper. Model caches and preferences remain outside the install tree. Captions already on screen survive Stop; unfinished inference is cancelled after a bounded drain.

Upload the ZIP files to `/var/lib/captionflow/releases` on `hetzner`, owned by `captionflow`. Upload `gpui-PLATFORM.json` last using an atomic rename. The service exposes these separately at `https://h.gskrd.me/apps/captionflow/v2/releases/PLATFORM`; legacy v1 updates remain separate. Verify HTTPS manifests and an actual component download after deployment. Component archives stay out of GitHub's user-facing download list.

The Cloudflare tunnel carries HTTP traffic; the `hetzner` SSH alias connects directly to the origin. SSH is rate limited, so reuse a connection with `ControlMaster=auto`, `ControlPersist=600` and `ControlPath=/tmp/captionflow-updates-%C` for SSH, SCP and rsync. Production HTTP checks must send the app's `User-Agent: CaptionFlow-Updater`; Cloudflare can reject the default Python user agent even while app requests succeed.

## Windows publisher signing

The workflow supports [Microsoft Artifact Signing](https://github.com/Azure/artifact-signing-action) using GitHub OIDC. No signing account is configured yet. The Ed25519 update signature authenticates updates; Windows also needs a trusted publisher signature for its publisher display.

1. Create an Azure Artifact Signing account, complete identity validation, and create a **Public Trust** certificate profile. See [Microsoft's setup guide](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart).
2. Create an Entra application and a federated credential for this repository's release branch. Grant **Artifact Signing Certificate Profile Signer** on the certificate profile. Use the GitHub OIDC issuer `https://token.actions.githubusercontent.com` and audience `api://AzureADTokenExchange`; configure the subject for each branch allowed to sign.
3. Configure repository variables `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `WINDOWS_SIGNING_ENDPOINT`, `WINDOWS_SIGNING_ACCOUNT`, and `WINDOWS_SIGNING_PROFILE`. Set `WINDOWS_SIGNING_ENABLED=true` only after these work.
4. Rebuild. The GPUI executable is signed before packaging; the installer is signed before installation tests. The workflow requires valid Authenticode signatures and RFC 3161 timestamps and fails if enabled signing fails.

Signing does not guarantee an immediate SmartScreen reputation pass. [Microsoft's reputation guidance](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation) explains why new signed apps may still prompt. A self-signed Windows certificate would not resolve this warning.

Speaker detection is optional. With Nemotron selected, enable the pinned ~107 MB Nemotron-3-Diarization companion or choose a compatible local Sortformer GGUF. It runs as a separate session-long stream; confident speaker labels color the corresponding source and translation rows consistently. Ambiguous overlapping turns stay neutral. A lagging or unavailable sidecar leaves captions running. The model downloads only during an authorized setup; other engines keep their normal caption colors.

Translation choices follow the [Hy-MT2 language list](https://huggingface.co/tencent/Hy-MT2-1.8B#supported-languages); the Mac inspector also accepts a custom language name. Translation accuracy and latency depend on the selected model and hardware. Each additional target needs inference work.
