# CaptionFlow

A native SwiftUI macOS app for large, sentence-sized live subtitles, with private MLX transcription and multilingual translation.

## Run

```sh
./scripts/build-app.sh
open dist/CaptionFlow.app
```

Build with Xcode 15.3+ / Swift 5.10+. Apple Silicon is required for MLX. The Swift shell targets macOS 14+; the current MLX wheels installed and tested here use macOS 26. Python 3.11+ is required for one-time runtime setup (default: `/opt/homebrew/bin/python3.11`; editable under Models). This is a locally signed development app, not a notarized distribution.

1. Under **Models**, click **Install local runtime**. This installs Python dependencies into a private virtual environment, without changing your global Python packages.
2. Optionally **Add folders…** or drop folders on the folder controls. Add several roots, a Hugging Face `hub` cache, or directly downloaded model directories. The scanner walks nested folders and Hugging Face `snapshots`, deduplicates roots, and ignores blob stores. Select a discovered model for each role.
3. Or leave roots empty: the app downloads its own Whisper Small and Qwen 2.5 1.5B 4-bit copies when you first start. Initial downloads are substantial (roughly 1.5 GB together).
4. Under **Languages**, choose microphone or Mac audio, a spoken language (or auto detection), and one or several translation languages. Choose Original, Translation, or Both.
5. Click **Start listening** (⌘R). Grant the macOS audio permission when prompted. Stop with ⌘R. Language, model, and input changes apply to the next session; appearance updates immediately.

**Style** controls typeface, size, text color, and background color. The toolbar’s full-screen button or **⌃⌘F** expands the same window into a content-only display: no toolbar, inspector, footer, or decorative labels. **Escape** or **⌃⌘F** exits full screen and restores the inspector. **⇧⌘P** shows sample text; **⌥⌘I** toggles the inspector in both windowed and full-screen modes. Full screen starts with the inspector hidden; its visibility is independent of the windowed layout. Captions are selectable for copying.

## Privacy and storage

- Swift captures microphone audio with AVAudioEngine or Mac audio with ScreenCaptureKit. Mac audio uses a display filter but registers only an audio output; no screen images are recorded.
- Audio is converted to 16 kHz mono float32 in memory and sent through a private subprocess pipe. No HTTP server is opened. Audio and transcripts are not written to disk or sent to a service.
- The Python worker uses **MLX Whisper** for speech and **MLX LM** for translation. Both run locally on Apple Silicon. The app is Swift; the inference runtime is Python, not an all-Swift MLX implementation.
- The app-managed runtime and model cache live under `~/Library/Application Support/CaptionFlow/`. Imported model roots are used in place, never moved or deleted. Preferences are stored in the app's UserDefaults domain.
- Turn off **Download missing models** for offline-only startup. The worker sets Hugging Face and Transformers offline mode, and resolves repositories using `local_files_only=True`. Existing local folders work without copying into the app cache. Dependencies and model files must already be installed.
- A root is a discovery location, not an automatic choice: explicitly select the discovered speech and translation models. MLX Whisper needs a converted config (`n_mels`, `n_audio_ctx`) and MLX weights. Translation requires MLX-compatible weights and tokenizer files. GGUF, incomplete snapshots, and arbitrary PyTorch checkpoints are not compatible.

## Streaming and caching

Whisper is a chunked recognizer, not a native streaming speech model. The worker re-decodes the active utterance about every 600 ms when compute is available. A simple energy gate ends an utterance after 800 ms of silence; continuous speech is bounded at 12 seconds. The latest sentence (up to 32 words / 220 characters) replaces the previous one, with provisional corrections allowed. Energy sensitivity is adjustable. Recognition and translation accuracy depend on the selected models, voice, noise, and language.

Each translation language owns an independent MLX KV cache. Every new hypothesis is tokenized with the same chat template. The worker finds the **longest common token prefix**, trims the previous cache (including generated output) back to that prefix, and feeds only the new suffix. This handles corrections and tokenizer boundary changes rather than assuming text only grows. Generated subword fragments are buffered to expose whole words (CJK text streams directly). Only models with safely trimmable, offset-addressable caches are accepted; the defaults and standard Llama models support this.

The native pipe and inference queue are bounded. Recognition revisions coalesce when inference falls behind, prioritizing recent audio over building an unlimited delay. Translation languages run sequentially on the same model. More languages, larger models, and slow hardware increase latency; this is not a guarantee of real-time performance. Punctuation and translation can change while speech develops. Long uninterrupted speech is segmented; no diarization or full transcript archive is implemented.

## Development and verification

```sh
swift test
python3.11 -m venv .venv
.venv/bin/python -m pip install -r Sources/CaptionFlow/Resources/worker/requirements.txt
.venv/bin/python scripts/test_worker.py
```

`swift test` covers model discovery (including nested snapshots, duplicate roots, and incompatible weights) and preferences. Python tests cover sentence rollover, language-aware word streaming, cache reuse boundaries, and bounded audio scheduling. `scripts/integration_check.py` exercises actual offline MLX transcription and cached translation with supplied local model paths; it does not download models.

Upstream libraries: [MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper), [MLX LM](https://github.com/ml-explore/mlx-lm). Default model cards: [Whisper Small](https://huggingface.co/mlx-community/whisper-small-mlx), [Qwen 2.5 1.5B Instruct 4-bit](https://huggingface.co/mlx-community/Qwen2.5-1.5B-Instruct-4bit).

## Verified on this Mac

- Swift build and two Swift tests passed; four Python tests passed.
- Development app bundle passes strict code-signature verification.
- Native UI verified: appearance preview, persisted typeface/colors/size, Control–Command–F into content-only full screen in the same `main` window, and Escape restoring controls and inspector.
- Real offline Whisper Small recognized synthesized “The train leaves at nine tomorrow morning.”
- Real Qwen translations streamed in French and Spanish. Cached output matched fresh greedy output for an original phrase, an extension, and a corrected time, with 50–51 prefix tokens reused.
- Full JSON-lines subprocess test passed: audio frames → speech → streamed French translation, with network access disabled in the worker configuration.
- These are pipeline/caching checks, not a translation-quality benchmark. The compact 1.5B model made wording and time-translation errors in the fixture. Select a larger compatible instruction model for better quality; meaning is not guaranteed by cache correctness.
- Microphone and ScreenCaptureKit hardware capture require user-granted macOS permissions and have not been verified using a live user recording.

`scripts/requirements-tested.txt` records the dependency versions used for this verification.
