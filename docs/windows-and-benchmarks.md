# CUDA-free Windows preview and performance lab

The Windows x64 preview uses a Qt desktop shell, stateful Nemotron 3.5 streaming ASR through NVIDIA NeMo-Speech.cpp 0.3.0, and Hy-MT2 1.8B Q4_K_M through llama.cpp b12110. Speech weights are Q8_0. The native macOS SwiftUI/MLX app remains available; the portable shell also runs on Mac with Metal for backend testing.

## Windows testing

Extract `dist/CaptionFlow-Windows-x64.zip` completely, then open **Start CaptionFlow.cmd**. Python 3.14.10, Qt, CPU/Vulkan native runtime archives and their dependencies are included. No Python installation, CUDA toolkit or CUDA runtime is required. GPU mode requires a functioning Vulkan driver. RTX 5000 Ada is the initial target, **not yet a verified device**.

On first launch choose whether to participate online. **No** disables all external app networking: downloads, uploads and update checks. Local authenticated loopback communication between the two inference processes still works. Offline machines need the pinned GGUF files copied into `portable/data/models`; the installer rebuilds absolute paths locally. A complete offline bundle can be assembled with `scripts/build-windows.py --offline-models` after downloading the models on an authorized online machine. **Yes** enables explicit model downloads (~1.9 GB), diagnostic metrics and signed release checks. Consent can be revoked under **Privacy / sharing**.

Select Vulkan and click **Download / verify models**, then start listening. For unavailable or failing Vulkan, explicitly select CPU and configure that profile. The app never silently labels CPU work as GPU execution. Choose the default microphone or Windows system audio. Audio stays in memory; a blocked transport stops with an error instead of silently losing speech. Windows capture, startup and GPU execution still require a physical Windows test; assembling its binaries on a Mac does not establish compatibility.

CPU profiles translate completed phrases using two translation threads, leaving speech recognition priority. GPU profiles also translate intermediate revisions. Disable translation if a machine cannot keep up. Model parameters, runtime binaries and fixtures are checksummed. The first load can include driver shader compilation and is not representative of warmed steady state.

## Overnight benchmarking

The app's **Start / resume benchmark** button prepares cached models/runtimes automatically, then tests the selected GPU backend and a separately labelled CPU fallback. **Until I stop** is the default; **Quick · 5 minutes**, **30 minutes** and **Overnight · 8 hours** are also available. Timed budgets begin after setup and fingerprinting; stopping may need a few seconds to flush/terminate inference. Each profile gets short throughput replay plus 60-second paced trials, testing right-context values 3, 0 and 6 with speech alone and English translation. Quick tests may cover only part of the matrix; the next run continues it.

The app saves a text-free checkpoint every five seconds and saves the active trial on Stop. Partial trials retain their measurements but do not receive accuracy or realtime pass claims. Resume restarts the model's decoder for the remaining test time; it does not attempt to serialize GPU/KV state. App/benchmark/dependency code, Python version, model/runtime checksums, fixture, hardware and profile settings identify each history. A change automatically creates a new subdirectory, preserving prior exports. Changing only the time budget keeps the same history.

Crashes, startup errors, a queue over ten seconds, or no source progress for fifteen seconds after a twenty-second startup grace are detected automatically. Failed realtime/accuracy gates also disable that profile. A failed backend probe skips its other profiles and proceeds to CPU. Failed profiles are not repeatedly retried in the same history. If all profiles fail, the run stops with saved diagnostics. An unexpected application/process exit preserves its latest checkpoint and quarantines that profile on the next launch. Normal Stop resumes the unfinished profile.

The benchmark prevents system sleep while running and restores normal sleep behaviour when finished. Opted-in clients upload new completed/partial records in batches, at most once every ten minutes during a run and again on Stop. Running checkpoints remain local; report uploads never contain transcripts.

The bundled French speech is synthetic. It verifies pipeline performance and catches transcription regressions on that phrase. It does **not** establish broad accent/noise accuracy, translation quality or microphone-to-screen latency. Add independently licensed, diverse fixtures with manually checked transcripts before making hardware recommendations. A fixture manifest names a mono PCM16 16 kHz WAV (up to 20 seconds), SHA-256, language, reference, kind and provenance. The harness repeats it with one second of silence. Use a matching source language in each configuration.

```sh
.venv-portable/bin/python portable/benchmark.py run \
  --config dist/portable-data/configuration.json \
  --fixture docs/fixtures/fr-synthetic.json \
  --output dist/benchmarks/my-machine --trial-seconds 60

.venv-portable/bin/python portable/benchmark.py aggregate \
  results-a/report.json results-b/report.json --output capabilities.json
```

Metrics include cold/process startup, first text, p50/p95/p99/max final-caption and final-translation lag, queue depth and drift, word error rate, revision erasure, process-tree resident memory and its slope, CPU use, missing final captions/translations, backend/device evidence and model/runtime/worker/fixture identities. Final lag is measured **after the input segment boundary**, which already includes the 0.8-second silence endpoint; it is not word latency. Throughput includes final translation drain, excludes model load, and uses an unpaced short clip. Paced runs include real scheduling contention. GPU utilization, VRAM high-water mark, temperature, power, capture drops and translation quality are explicitly unknown where unmeasured. CPU usage currently covers the worker process, not its separate translation child; process-tree RSS can double-count shared pages.

A realtime fixture gate requires: no missing source/translation finals, p95 source lag <=2 s, maximum source lag <=5 s, p95 translation lag <=3 s when enabled, queue growth <=0.01 s/s, and WER <=25%. A separate headroom gate requires throughput RTF <=0.7. Neither alone proves suitability. Every result retains fixture scope; aggregation reports counts and failures rather than inventing a universal minimum machine. A provisional four-core/8 GB test floor is an investigation target, not a supported minimum.

`journal.sqlite` commits each completed trial; `report.json` is atomically replaced. Exports are deduplicated by run/trial IDs and grouped by hardware, model/code identities, fixture and runtime version. Native crashes, load failures, timeouts and cancelled runs are isolated from the next profile. Detailed failure tails stay local, with user/home/app path redaction, for explicit review and sharing.

## Opt-in reports and releases

The deployed service is `https://h.gskrd.me/apps/captionflow`, hosted on Hetzner at loopback port 5052 behind the existing Cloudflare tunnel. It runs as the isolated `captionflow` system user. Source lives in `/opt/apps/captionflow/current`; private SQLite state and releases live in `/var/lib/captionflow`. Public endpoints expose health, enrollment, submission, self-deletion and signed release metadata; there is no public report-listing or administrative publishing endpoint. Cloudflare necessarily handles connection metadata; the application does not persist IP addresses. Reports expire after 30 days, with hourly cleanup.

Opted-in portable clients upload allowlisted hardware/benchmark data and hashed error fingerprints. Raw error messages, native log tails, audio, recognized text and translated text are not automatically uploaded. The native Mac app sends hardware/error fingerprints and checks releases; the portable companion supplies the benchmark UI. Full local diagnostic bundles remain an explicit export action. Revocation disables future internet requests even if server deletion fails; the UI reports that failure.

Access submissions during a debugging session:

```sh
ssh hetzner 'sudo -u captionflow python3 /opt/apps/captionflow/current/server.py --export /var/lib/captionflow/export.json'
scp hetzner:/var/lib/captionflow/export.json ./captionflow-submissions.json
```

The service collects evidence; it does not itself run a coding agent or autonomously modify/publish the app. Fixes still need implementation, tests and an explicit release. The signing private key stays at `~/.config/captionflow/release-signing.key` on the maintainer Mac (mode 0600), outside source control and off the VPS. Back it up securely. Clients embed the public Ed25519 key, reject wrong-platform/expired/tampered manifests and verify the archive SHA-256. A newer valid release reveals the update button only to opted-in clients. Clicking downloads a verified ZIP; replacement is manual after quitting, preserving the data directory. This is not an in-place automatic installer.

```sh
.venv-portable/bin/python scripts/build-windows.py
.venv-portable/bin/python scripts/sign-release.py dist/CaptionFlow-Windows-x64.zip \
  --version 0.2.1 --sequence 2 --platform windows-x86_64 --output dist/windows-x86_64.json
```

Publish archives first, then atomically replace the platform JSON under `/var/lib/captionflow/releases` using administrator SSH access. Increase the sequence monotonically; do not sign an untested artifact as stable. The current Windows artifact is an explicitly unverified preview.

## Research decisions, 9 November 2026

- **NeMo-Speech.cpp / Vulkan:** primary portable path. Official native Windows CPU/Vulkan releases and a stable streaming C ABI support Nemotron 3.5. [Build](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/build.md), [SDK](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/sdk.md), [model](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b).
- **ZML:** its documented NVIDIA backend requires CUDA; no documented turnkey Windows speech path. It does not fit the strict CUDA-free target. [Project](https://github.com/zml/zml#running-models-on-gpu--tpu).
- **tinygrad/OpenCL:** CL remains an available runtime. Worth an experimental profile if a working model adapter is supplied, but not equivalent to a validated streaming ASR integration. [Runtimes](https://docs.tinygrad.org/runtime/).
- **whisper.cpp/Vulkan:** viable fallback with broad support; its rolling-window streaming example repeatedly decodes audio rather than maintaining a native streaming encoder. [Vulkan](https://github.com/ggml-org/whisper.cpp#vulkan-gpu-support).
- **sherpa-onnx:** a promising second adapter for small CPU streaming models and multilingual int8 Nemotron. Needed before claiming support for genuinely weak PCs. [Project](https://github.com/k2-fsa/sherpa-onnx).
- **DirectML:** another Windows option, but validate graph placement and CPU fallback. The execution provider is in sustained engineering. [Microsoft documentation](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html).
- **Granite Speech 5:** multilingual/translation/hotword candidate. MLX `stream=True` streams output from already encoded recordings; it does not establish live input support. [MLX Audio](https://github.com/Blaizzy/mlx-audio/blob/main/docs/models/stt/granite-speech.md).
- **Parakeet Redux:** attractive small model, but headline throughput belongs to its specialized Photon backend and tested hardware. Benchmark generic MLX/portable adapters separately. [Model](https://huggingface.co/moondream/parakeet-redux).
- **Phonon 2.5:** custom archive/cache handling; its documented MLX backend does not yet stream live input. [Adapter](https://github.com/Blaizzy/mlx-audio/blob/main/mlx_audio/stt/models/phonon2/README.md).
- **Hy-MT2 7B:** quality candidate using the existing family prompt; not the low-memory default. [Model](https://huggingface.co/mlx-community/Hy-MT2-7B-4bit).
- **TranslateGemma 4B:** requires its structured translation prompt and language codes; generic instruction prompts are insufficient. [Official model](https://huggingface.co/google/translategemma-4b-it).

Only Nemotron and the existing Hy-MT2 family are integrated in this portable preview. The other candidates remain research entries, not untested selectable promises. Existing MLX Audio 0.5.4 installations are not upgraded by this work.

## Delivery verification

- 26 Swift tests, 17 worker tests and 11 portable/service tests passed. The signed macOS release passed strict code-signature verification; its new privacy control was observed in the native app.
- A first-launch portable UI test chose local-only mode and observed zero HTTP requests. Offline Windows configuration and DLL provisioning were exercised using the real Windows archives on Mac, without executing those Windows binaries.
- The final Metal acceptance run replayed 184.9 seconds of synthetic French with English translation, produced all 30 final captions/translations, and confirmed 33 translation layers offloaded. P95 source final lag was 0.076 s and p95 translation final lag was 0.362 s after segmentation boundaries. Short-clip throughput RTF was 0.251. These are fixture results, not general latency/accuracy guarantees.
- A 92.5-second CPU test with phrase-only translation passed the same fixture gate. Frequent translation revisions failed the initial sustained CPU gate despite a passing short-clip throughput test.
- Hetzner health, enrollment, submission, privacy-field rejection, self-deletion, signed manifest verification and the complete public Windows ZIP download were exercised. The first opted-in native Mac startup diagnostic was received through the production service.
- Windows hardware execution, microphone capture, WASAPI loopback, Vulkan on RTX 5000 Ada and a complete eight-hour endurance run remain unverified. No minimum PC specification is certified.

Windows 0.2.1 preview: [download](https://h.gskrd.me/apps/captionflow/releases/CaptionFlow-Windows-x64-0.2.1.zip), 211,254,174 bytes, SHA-256 `e1e9daf6de871ea7a3cd3c08e609bac99b5a6af0d0b4f45d633a55b73863fb1d`. For an offline update, keep the existing `portable/data` directory, including its models and runtime files; no model redownload is required.

## Stop/resume update (0.2.1)

Verified with real Metal inference: Stop saved 7.36 seconds of an active test, then resume continued the same run ID for the remaining test time. A deliberately stalled subprocess was detected and terminated after about twenty seconds. Automated tests cover crash recovery, persistent failure skipping, GPU-to-CPU progression, partial checkpoints, short time budgets, update invalidation, and bounded incremental uploads. Windows execution still requires physical hardware validation.
