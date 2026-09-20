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
3. Or leave roots empty: choose Whisper Small, Parakeet TDT 0.6B v3, or Voxtral Realtime 4B 4-bit and the app downloads its own copy when first used. Translation offers the translation-specific Hy-MT2 1.8B 4-bit model, Qwen 3.5 in 0.8B and 2B 4-bit variants, and Qwen 2.5 1.5B 4-bit. Model downloads are substantial.
4. Under **Languages**, choose microphone or Mac audio, a spoken language (or auto detection), and one or several translation languages. Choose Original, Translation, or Both.
5. Click **Start listening** (⌘R). Grant the macOS audio permission when prompted. Stop with ⌘R. Language, model, and input changes apply to the next session; appearance updates immediately.

**Style** controls typeface, size, text color, and background color. The toolbar’s full-screen button or **⌃⌘F** expands the same window into a content-only display: no toolbar, inspector, footer, or decorative labels. **Escape** or **⌃⌘F** exits full screen and restores the inspector. **⇧⌘P** shows sample text; **⌥⌘I** toggles the inspector in both windowed and full-screen modes. Full screen starts with the inspector hidden; its visibility is independent of the windowed layout. Words appear progressively in stable, left-aligned rows.

## Privacy and storage

- Swift captures microphone audio with AVAudioEngine or Mac audio with ScreenCaptureKit. Mac audio uses a display filter but registers only an audio output; no screen images are recorded.
- Audio is converted to 16 kHz mono float32 in memory and sent through a private subprocess pipe. No HTTP server is opened. Audio and transcripts are not written to disk or sent to a service.
- The Python worker supports **MLX Whisper**, **Parakeet TDT**, and **Voxtral Realtime** for speech, with **MLX LM** for translation. All inference runs locally on Apple Silicon. The app is Swift; the inference runtime is Python.
- The app-managed runtime and model cache live under `~/Library/Application Support/CaptionFlow/`. Imported model roots are used in place, never moved or deleted. Preferences are stored in the app's UserDefaults domain.
- Turn off **Download missing models** for offline-only startup. The worker sets Hugging Face and Transformers offline mode, and resolves repositories using `local_files_only=True`. Existing local folders work without copying into the app cache. Dependencies and model files must already be installed.
- A root is a discovery location, not an automatic choice: explicitly select the discovered speech and translation models. MLX Whisper needs a converted config (`n_mels`, `n_audio_ctx`) and MLX weights. Translation requires MLX-compatible weights and tokenizer files. GGUF, incomplete snapshots, and arbitrary PyTorch checkpoints are not compatible.

## Streaming and caching

Whisper and Parakeet re-decode the active utterance about every 600 ms when compute is available. Voxtral uses its stateful realtime decoder and consumes each audio frame once. A simple energy gate ends an utterance after 800 ms of silence; continuous speech is bounded at 12 seconds. Each audio utterance has a stable ID. A native queue reveals the first recognition and translation one word every 100 ms. Recognition preserves words already read and corrects its unpublished suffix. Translation revisions are different: the prior readable result stays in place while the model regenerates, then only changed words are replaced when the candidate is complete. Terminal punctuation can be corrected as a phrase grows. A sentence changes when the next sentence’s first word is ready; measured word rows paginate after three lines when necessary. Fixed row positions prevent text from re-centering on every word. Energy sensitivity is adjustable. Recognition and translation accuracy depend on the selected models, voice, noise, and language.

Each translation language owns an independent MLX KV cache. Every new hypothesis is tokenized with the same chat template. For models with a safely trimmable, offset-addressable cache, the worker finds the **longest common token prefix**, trims the previous cache (including generated output) back to that prefix, and feeds only the new suffix. This handles corrections and tokenizer boundary changes rather than assuming text only grows. Generated subword fragments are buffered to expose whole words (CJK text streams directly). Models whose MLX cache cannot be rewound use a fresh-decode fallback.

Hy-MT2 uses its documented user-only translation instruction because the model has no default system prompt. The bundled 4-bit MLX conversion is about 1.02 GB and supports the model's 33 translation languages.

Qwen 3.5 currently uses a non-rewindable MLX cache. CaptionFlow supports it with a safe fresh-decode fallback for each partial revision; Qwen 2.5 retains the faster editable-prefix path. The smaller Qwen 3.5 model reduces download size and resident weight memory, but fresh partial decoding can offset some of its speed advantage during rapidly changing speech.

The native pipe and inference queues are bounded. Recognition revisions coalesce when inference falls behind, prioritizing recent audio over building an unlimited delay. Translation runs on its own latest-first queue so generation never pauses the speech decoder; superseded partial hypotheses are dropped while final hypotheses are retained. Translation languages still run sequentially on the same model. More languages, larger models, and slow hardware increase latency; this is not a guarantee of real-time performance. Punctuation and translation can change while speech develops. Long uninterrupted speech is segmented; no diarization or full transcript archive is implemented.

## Development and verification

```sh
swift test
python3.11 -m venv .venv
.venv/bin/python -m pip install -r Sources/CaptionFlow/Resources/worker/requirements.txt
.venv/bin/python scripts/test_worker.py
```

`swift test` covers model discovery (including Parakeet and Voxtral, nested snapshots, duplicate roots, and incompatible weights) and preferences. Python tests cover speech-engine detection, sentence rollover, language-aware word streaming, cache reuse boundaries, and bounded audio scheduling. `scripts/integration_check.py` exercises Whisper plus cached translation. `scripts/asr_integration_check.py` exercises the Parakeet and Voxtral JSON-lines worker paths with supplied local models. Neither script downloads models.

Upstream libraries: [MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper), [MLX Audio](https://github.com/Blaizzy/mlx-audio), and [MLX LM](https://github.com/ml-explore/mlx-lm). Model cards: [Whisper Small](https://huggingface.co/mlx-community/whisper-small-mlx), [Parakeet TDT 0.6B v3](https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v3), [Voxtral Realtime 4B 4-bit](https://huggingface.co/mlx-community/Voxtral-Mini-4B-Realtime-2602-4bit), [Hy-MT2 1.8B MLX 4-bit](https://huggingface.co/mlx-community/Hy-MT2-1.8B-4bit), [Qwen 3.5 0.8B MLX 4-bit](https://huggingface.co/mlx-community/Qwen3.5-0.8B-MLX-4bit), [Qwen 3.5 2B MLX 4-bit](https://huggingface.co/mlx-community/Qwen3.5-2B-MLX-4bit), and [Qwen 2.5 1.5B Instruct 4-bit](https://huggingface.co/mlx-community/Qwen2.5-1.5B-Instruct-4bit).

## Verified on this Mac

- Swift build and eighteen Swift tests passed; seven Python tests passed.
- Development app bundle passes strict code-signature verification with normal Keychain access. The certificate-backed designated requirement remains identical across a rebuild.
- Native UI verified: appearance preview, persisted typeface/colors/size, Control–Command–F into content-only full screen in the same `main` window, and Escape restoring controls and inspector.
- Real offline Whisper Small recognized synthesized “The train leaves at nine tomorrow morning.”
- Real Parakeet TDT 0.6B v3 recognized “The train leaves at 9 tomorrow morning.” through the packaged worker.
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
