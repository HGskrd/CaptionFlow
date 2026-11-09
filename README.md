# CaptionFlow

A native SwiftUI macOS app for large, sentence-sized live subtitles, with private MLX transcription and multilingual translation.

## Run

```sh
./scripts/build-app.sh
open dist/CaptionFlow.app
```

Build with Xcode 15.3+ / Swift 5.10+. Apple Silicon is required for MLX. The Swift shell targets macOS 14+; the current MLX wheels installed and tested here use macOS 26. Python 3.11+ is required for one-time runtime setup (default: `/opt/homebrew/bin/python3.11`; editable under Models). This is a locally signed development app, not a notarized distribution.

The app bundle uses an optimized release build by default. Use `CONFIGURATION=debug ./scripts/build-app.sh` when debugging Swift code.

1. Under **Models**, click **Install local runtime**. This installs Python dependencies into a private virtual environment, without changing your global Python packages.
2. Optionally **Add folders…** or drop folders on the folder controls. Add several roots, a Hugging Face `hub` cache, or directly downloaded model directories. The scanner walks nested folders and Hugging Face `snapshots`, deduplicates roots, and ignores blob stores. Select a discovered model for each role.
3. Or leave roots empty: choose Whisper Small, Parakeet TDT 0.6B v3, Qwen3-ASR 1.7B 4-bit, or Voxtral Realtime 4B 4-bit and the app downloads its own copy when first used. Translation offers the translation-specific Hy-MT2 1.8B 4-bit model, Qwen 3.5 in 0.8B and 2B 4-bit variants, and Qwen 2.5 1.5B 4-bit. Model downloads are substantial.
4. Under **Languages**, choose microphone or Mac audio, a spoken language (or auto detection), and one or several translation languages. Choose Original, Translation, or Both.
5. Click **Start listening** (⌘R). Grant the macOS audio permission when prompted. Stop with ⌘R. Language, model, and input changes apply to the next session; appearance updates immediately.

**Style** controls typeface, size, text color, and background color. The toolbar’s full-screen button or **⌃⌘F** expands the same window into a content-only display: no toolbar, inspector, footer, or decorative labels. **Escape** or **⌃⌘F** exits full screen and restores the inspector. **⇧⌘P** shows sample text; **⌥⌘I** toggles the inspector in both windowed and full-screen modes. Full screen starts with the inspector hidden; its visibility is independent of the windowed layout. Words appear progressively in stable, left-aligned rows.

## Privacy and storage

- Swift captures microphone audio with AVAudioEngine or Mac audio with ScreenCaptureKit. Mac audio uses a display filter but registers only an audio output; no screen images are recorded.
- Audio is converted to 16 kHz mono float32 in memory and sent through a private subprocess pipe. No HTTP server is opened. Audio and transcripts are not written to disk or sent to a service.
- The Python worker supports **MLX Whisper**, **Parakeet TDT**, **Qwen3-ASR**, and **Voxtral Realtime** for speech, with **MLX LM** for translation. All inference runs locally on Apple Silicon. The app is Swift; the inference runtime is Python.
- The app-managed runtime and model cache live under `~/Library/Application Support/CaptionFlow/`. Imported model roots are used in place, never moved or deleted. Preferences are stored in the app's UserDefaults domain.
- Turn off **Download missing models** for offline-only startup. The worker sets Hugging Face and Transformers offline mode, and resolves repositories using `local_files_only=True`. Existing local folders work without copying into the app cache. Dependencies and model files must already be installed.
- A root is a discovery location, not an automatic choice: explicitly select the discovered speech and translation models. MLX Whisper needs a converted config (`n_mels`, `n_audio_ctx`) and MLX weights. Translation requires MLX-compatible weights and tokenizer files. GGUF, incomplete snapshots, and arbitrary PyTorch checkpoints are not compatible.

## Streaming and caching

For Voxtral, **Models → Fast draft + corrections (experimental)** enables two local sessions on the same audio. Defaults are a 240 ms draft delay and a 1,200 ms correction delay. Both delays are adjustable; single-session recognition retains its 480 ms default. These values are audio lookahead, not total observed latency. The native word cadence and model processing add delay.

The sessions share one loaded weight set and encode incoming audio once. Decoder caches remain independent, and the audio pipeline forks only at utterance end to preserve each delay's silence padding and finalization. Delay conditioning is computed once per delay and restored before decoding. Legacy checkpoints can contain float32 normalization, bias, and conditioning parameters alongside float16 weights; the worker aligns these floating parameters to the convolution weight dtype in memory, preventing accidental float32 execution throughout both transformers. Quantized integer weights and model files are unchanged; intentionally float32 models remain float32. Whole-word alignment replaces the slower transcript's prefix while keeping the fast stream's newer suffix. Without any matching words, the draft remains until overlap or finalization; the final slow result is authoritative. Ambiguous or repeated words can still align incorrectly, so this mode is experimental. Visible source words may change, and translation follows the combined revised source. Other speech backends keep their existing behavior. Model and delay settings apply on the next listening session.

Whisper, Parakeet, and Qwen3-ASR re-decode the active utterance about every 600 ms when compute is available. The current MLX Qwen3-ASR implementation generates incrementally from a fixed audio buffer, so CaptionFlow uses coalesced snapshots rather than treating it as a persistent audio stream. Voxtral uses its stateful realtime decoder and consumes each audio frame once. A simple energy gate ends an utterance after 800 ms of silence; continuous speech is bounded at 12 seconds. Each audio utterance has a stable ID. A native queue reveals the first recognition and translation one word at a time. Its damped 120–240 ms cadence follows recent batch delivery without mirroring every inference-time spike, avoiding fast bursts between slow decodes. Recognition preserves words already read and corrects its unpublished suffix. Translation revisions are different: the prior readable result stays in place while the model regenerates, then only changed words are replaced when the candidate is complete. Terminal punctuation can be corrected as a phrase grows. A sentence changes when the next sentence’s first word is ready; measured word rows paginate after three lines when necessary. Fixed row positions prevent text from re-centering on every word. Energy sensitivity is adjustable. Recognition and translation accuracy depend on the selected models, voice, noise, and language.

Each translation language owns an independent MLX KV cache. Every new hypothesis is tokenized with the same chat template. For models with a safely trimmable, offset-addressable cache, the worker finds the **longest common token prefix**, trims the previous cache (including generated output) back to that prefix, and feeds only the new suffix. This handles corrections and tokenizer boundary changes rather than assuming text only grows. Generated subword fragments are buffered to expose whole words (CJK text streams directly). Models whose MLX cache cannot be rewound use a fresh-decode fallback.

Hy-MT2 uses its documented user-only translation instruction because the model has no default system prompt. The bundled 4-bit MLX conversion is about 1.02 GB and supports the model's 33 translation languages.

Qwen 3.5 currently uses a non-rewindable MLX cache. CaptionFlow supports it with a safe fresh-decode fallback for each partial revision; Qwen 2.5 retains the faster editable-prefix path. The smaller Qwen 3.5 model reduces download size and resident weight memory, but fresh partial decoding can offset some of its speed advantage during rapidly changing speech.

The native pipe and inference queues are bounded. Snapshot recognition revisions coalesce when inference falls behind. Voxtral instead batches up to 400 ms of already queued audio without dropping samples, waiting for a batch, or crossing utterance boundaries. Its backlog limit measures 25 seconds of actual samples, independently of capture packet size. Translation runs on its own latest-first queue; superseded partial hypotheses are dropped while final hypotheses are retained. Partial translation starts are spaced at least 600 ms apart, and new translations yield when more than 200 ms of speech audio is queued. Finals bypass the cadence limit and shutdown drains retained results. Translation languages still run sequentially on the same model, and both models compete for the same GPU. More languages, larger models, and slow hardware increase latency; this is not a guarantee of real-time performance. Punctuation and translation can change while speech develops. Long uninterrupted speech is segmented; no diarization or full transcript archive is implemented.

## Development and verification

```sh
swift test
python3.11 -m venv .venv
.venv/bin/python -m pip install -r Sources/CaptionFlow/Resources/worker/requirements.txt
.venv/bin/python scripts/test_worker.py
.venv/bin/python scripts/test_dual_delay.py
.venv/bin/python scripts/test_voxtral_runtime.py
```

`swift test` covers model discovery (including Parakeet, Qwen3-ASR, and Voxtral, nested snapshots, duplicate roots, and incompatible weights) and preferences. Python tests cover speech-engine detection, sentence rollover, language-aware word streaming, cache reuse boundaries, and bounded audio scheduling. `scripts/integration_check.py` exercises Whisper plus cached translation. `scripts/asr_integration_check.py` exercises the Parakeet, Qwen3-ASR, and Voxtral JSON-lines worker paths with supplied local models. Neither script downloads models.

`scripts/test_voxtral_runtime.py` checks MLX execution dtype and numerical agreement using tiny random weights, without downloads. `scripts/streaming_performance_check.py VOXTRAL_PATH --translation HY_MT2_PATH` synthesizes a minute of French speech, feeds it at live speed, and checks English translation plus every utterance's final lag. Use `--seconds 180` for a longer run, `--worker dist/CaptionFlow.app/Contents/Resources/worker/worker.py` to verify the signed bundle, and the app runtime's Python to match the installed environment. The shared-encoder adapter targets the pinned MLX Audio 0.5.4 internals; rerun these checks and `scripts/dual_delay_check.py` before upgrading that dependency.

Upstream libraries: [MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper), [MLX Audio](https://github.com/Blaizzy/mlx-audio), and [MLX LM](https://github.com/ml-explore/mlx-lm). Model cards: [Whisper Small](https://huggingface.co/mlx-community/whisper-small-mlx), [Parakeet TDT 0.6B v3](https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v3), [Qwen3-ASR 1.7B 4-bit](https://huggingface.co/mlx-community/Qwen3-ASR-1.7B-4bit), [Voxtral Realtime 4B 4-bit](https://huggingface.co/mlx-community/Voxtral-Mini-4B-Realtime-2602-4bit), [Hy-MT2 1.8B MLX 4-bit](https://huggingface.co/mlx-community/Hy-MT2-1.8B-4bit), [Qwen 3.5 0.8B MLX 4-bit](https://huggingface.co/mlx-community/Qwen3.5-0.8B-MLX-4bit), [Qwen 3.5 2B MLX 4-bit](https://huggingface.co/mlx-community/Qwen3.5-2B-MLX-4bit), and [Qwen 2.5 1.5B Instruct 4-bit](https://huggingface.co/mlx-community/Qwen2.5-1.5B-Instruct-4bit).

## Verified on this Mac

- Performance update (2026-11-09, M4 Pro / 48 GB): the same 60-second synthesized French-to-English fixture with dual Voxtral and Hy-MT2 changed from steadily growing final-caption lag (6.96–31.41 seconds) to 0.76–0.82 seconds; all five final French transcripts matched exactly. The signed release then passed 180 seconds of paced speech with 15 final captions at 0.71–0.91 seconds lag and final English translation completing 0.17 seconds after input ended. These are worker-protocol measurements using synthesized audio, not microphone-to-screen latency or a general accuracy benchmark. [Recorded measurements](docs/performance-2026-11-09.json).
- The speech-only microprofile improved from 12.35 to 4.10 seconds for the same 10.82-second input, excluding model load. 26 Swift tests and 17 Python tests passed, including execution-dtype/numerical checks, exact PCM preservation, duration-based backlog bounds, conditioning reuse, independent finalization state, and translation scheduling. Packaged dual-delay output matched the single-session reference. Strict signature verification passed; the relaunched native app was observed at **Ready to listen**.
- Dual-delay recognition: 26 Swift tests and 10 Python tests cover corrected read words, insertions/deletions, retained word/time windows, shared-model delay conditioning, final events, and older preferences. The packaged worker passed a paced 240/1,200 ms check with local Qwen French translation; its final transcript matched the standalone 1,200 ms reference. The synthesized sentence needed no wording correction, so actual changed-word display is covered by deterministic decoder and native caption-state tests. Native control layout could not be visually checked because computer use returned `cgWindowNotFound`.
- Rerun with `scripts/dual_delay_check.py MODEL_PATH --translation MODEL_PATH --worker dist/CaptionFlow.app/Contents/Resources/worker/worker.py` using the app runtime's Python. Set `PYTHONDONTWRITEBYTECODE=1` when running Python directly inside a signed bundle; the acceptance script does this for its worker subprocess.

- Swift build and eighteen Swift tests passed; seven Python tests passed.
- Development app bundle passes strict code-signature verification with normal Keychain access. The certificate-backed designated requirement remains identical across a rebuild.
- Native UI verified: appearance preview, persisted typeface/colors/size, Control–Command–F into content-only full screen in the same `main` window, and Escape restoring controls and inspector.
- Real offline Whisper Small recognized synthesized “The train leaves at nine tomorrow morning.”
- Real Parakeet TDT 0.6B v3 recognized “The train leaves at 9 tomorrow morning.” through the packaged worker.
- Real Qwen3-ASR 1.7B 4-bit recognized “The train leaves at nine tomorrow morning.” exactly through the packaged worker while Hy-MT2 was resident and produced the final French translation.
- Real Voxtral Realtime 4B 4-bit recognized the same sentence through its stateful decoder, producing six incremental source updates before the final caption.
- Real Qwen translations streamed in French and Spanish. Cached output matched fresh greedy output for an original phrase, an extension, and a corrected time, with 50–51 prefix tokens reused.
- Real Qwen 3.5 0.8B MLX 4-bit translated two successive English revisions into French through the fresh-decode fallback; the corrected time changed from nine to ten without a worker restart.
- Real Qwen 3.5 2B MLX 4-bit also translated and revised the fixture correctly. On this Mac it took 0.280 s for the first short translation and 0.229 s for a revision, compared with 0.217 s and 0.154 s for Qwen 2.5 1.5B using its editable prefix cache. This single fixture is a latency check, not a quality benchmark.
- Real Hy-MT2 1.8B MLX 4-bit translated a French sentence into English in 0.365 s, then revised it in 0.135 s while reusing 43 cached prompt tokens. Its model-specific user-only prompt produced clean target-language output in both revisions.
- Parakeet and Voxtral each passed while the Qwen translation model was resident in the same offline MLX worker.
- Full JSON-lines subprocess test passed: audio frames → speech → streamed French translation, with network access disabled in the worker configuration.
- These are pipeline/caching checks, not a translation-quality benchmark. The compact 1.5B model made wording and time-translation errors in the fixture. Select a larger compatible instruction model for better quality; meaning is not guaranteed by cache correctness.
- Live Mac audio capture verified after refreshing the Screen & System Audio Recording permission for the persistently signed app: source captions and English translation appeared while capture was running. Microphone capture remains unverified with a live recording.

`scripts/requirements-tested.txt` records the dependency versions used for this verification.

## Persistent local signing and capture permissions

Ad-hoc signing binds macOS privacy grants to an individual executable hash. Rebuilding used to change that hash even though System Settings still displayed an enabled CaptionFlow entry. The macOS TCC log confirmed a mismatch between the granted and running code requirements.

Builds now use a certificate-backed local development identity named `CaptionFlow Local Development`, stored in the login Keychain. No trusted root, Keychain password file, or privacy grant is installed. The signing key is imported as non-extractable with access for `/usr/bin/codesign`; temporary private-key files are deleted. The build refuses to silently fall back to ad-hoc signing. A separately installed Apple signing identity may instead be supplied in `CAPTIONFLOW_SIGNING_IDENTITY`.

On a new development Mac, the one-time identity creation requires the owner's explicit approval:

```sh
/usr/bin/python3 scripts/sign-app.py dist/CaptionFlow.app --create-identity
./scripts/build-app.sh
```

After migrating from the earlier ad-hoc build, the owner must refresh the old Screen & System Audio Recording permission once: quit CaptionFlow, remove its stale entry, add the current `dist/CaptionFlow.app`, enable it, and reopen the app. Subsequent builds signed with the same certificate should keep the same designated requirement. The app checks permission for the currently running build before starting Mac audio and explains the stale-grant case instead of reporting that the user declined.

Apple reference: [Inside Code Signing: Requirements](https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements).

## Windows preview, benchmark lab and opt-in feedback

A CUDA-free Windows x64 preview now uses native Nemotron 3.5 streaming recognition and Hy-MT2 through Vulkan, with CPU fallback. `scripts/build-windows.py` assembles an embedded-Python ZIP; `portable/app.py` provides the desktop shell, overnight benchmark controls, diagnostic export and explicit first-launch internet consent. The same portable shell can exercise Metal on Mac. Physical Windows capture and RTX 5000 Ada execution remain unverified.

Both desktop shells now ask about online participation before external networking. Declining disables runtime/model downloads, feedback uploads and update checks; offline files can still be used. Opted-in clients report hardware and error fingerprints to the Hetzner service; the portable benchmark additionally submits performance metrics without audio, captions or translations. Signed update discovery is implemented; downloaded updates are installed manually after quitting.

See [Windows setup, benchmark methodology, research and service operations](docs/windows-and-benchmarks.md) and [local native measurements](docs/portable-performance-2026-11-09.json). There is no established weakest supported PC yet.
