# CaptionFlow GPUI

GPUI is the release interface on Windows, macOS and Linux. Mac uses the shared native
capture engine; Windows and Linux connect the same view to the existing Python capture
and inference backend through private JSON lines. Qt supplies portable native
dialogs and caption layout; its application window stays hidden.

Run `./script/build_and_run.sh --verify` from the repository root. Build both Mac
installers with `scripts/release-macos.sh`. Windows builds in GitHub Actions.
See [release instructions](../docs/releases.md) for signed component updates,
packaging, signing setup, and acceptance requirements.

Validation: `cargo test --locked`, `cargo fmt --check`, and
`cargo clippy --locked --all-targets -- -D warnings` inside this directory;
`./script/test_native.sh` and the Python suites at the repository root.

Linux packages and Arch/EndeavourOS setup are described in [Linux installation](../docs/linux.md).
