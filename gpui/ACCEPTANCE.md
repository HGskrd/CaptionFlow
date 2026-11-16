# Mac acceptance — 2026-11-12

The GPUI frontend is linked to the current native engine, rather than an
independent simulation. The shared native sources and tests include the current
transport-preservation, requirements-readiness, and graceful-drain fixes from the
existing development checkout. That checkout's uncommitted work was left intact.

Validated on this Mac:

- 43 shared Swift XCTest cases, including transport overload, failed workers,
  retained audio, drain timeout, model discovery, and caption flow.
- 13 Python worker tests and 4 dual-delay tests.
- Two Rust caption paging tests, formatting, and Clippy with warnings denied.
- Actual offline Whisper Small recognition with Hy-MT2 translation, using the new
  GPUI Python environment, cached model files, and synthesized English audio.
  Recognition returned “The train leaves at 9 tomorrow morning.”; final French
  translation was present. Worker protocol completed without errors in 26.58 s.
- Strict nested app/library signature verification and byte comparison of all
  eight bundled worker files with source.
- Native window launch; saved font updates and progressive appearance preview;
  runtime readiness and discovery of nine cached model folders; speech model
  selection; native attached privacy and folder-import sheets; local-only consent.
- Native macOS fullscreen removes the app toolbar, status/notification bars,
  language labels, and inspector. The inspector can be explicitly opened from
  View. No keyboard shortcut hints remain in the GPUI canvas, inspector, or buttons;
  menu items use native key equivalents.

The initial GPUI settings import contained a saved Phonon selection from another
build. The first offline acceptance used Whisper Small and Hy-MT2. The user then
selected Voxtral Realtime with fast draft/corrections (240/1200 ms) and Hy-MT2;
those choices were preserved for the final live test. The other app's preferences
and installed Python packages were left intact.

Final live verification after the Mac audio permission was granted:

- The bundled Voxtral dual-delay worker passed a paced French-speech check in
  GPUI's runtime: first text at 1.24 s, 27 source updates, 19 completed translations,
  and final recognition lag of 0.817 s.
- A synthesized French fixture was played through `/usr/bin/afplay` while the
  actual GPUI app captured **Mac audio**. Capture reported nonzero RMS
  (approximately 0.17–0.24), 13 source events and 66 translation events during the
  fixture. Both native caption character counts and translations increased.
- The visible GPUI canvas displayed the recognized French train/station sentence
  and its English translation. This verifies capture → worker → native pacing →
  GPUI rendering, rather than only the worker protocol.

The previous empty Listening state did not distinguish missing/silent input from
recognition. The app now reports the selected input and captured seconds, gives a
more specific empty state, and surfaces PCM/conversion errors. Operational counters
are written locally to `~/Library/Application Support/CaptionFlow/GPUI/diagnostics/current.json`;
these contain no audio or caption text. Mac audio captures apps playing on the
Mac; microphone input must be selected separately. Microphone recording was not
part of the final test.

Reproduce the shared native suite with `./script/test_native.sh`. It explicitly
uses Xcode's Swift compiler, XCTest modules/libraries, and XCTest runner with the
standalone SDK. The default command-line test runner reported zero discovered
tests on this machine; the direct XCTest runner executed all 43 cases.

Display parity correction uses the newer release sources in the separate
`eventtranscribe-benchmark` checkout as the reference. It restores independent
Rolling lines, Phrase blocks, Transcript, and Classic modes; 2–4 visible lines;
Never/5 s/10 s silence clearing; and gentle animation with Reduce Motion support.
The 11 additional Swift tests cover corrected utterances, stable line identity,
bounded transcript history, silence clearing/restoration, and semantic linked
scrolling through updates and resizing. GPUI uses the same document/layout/scroll
mapping with virtualized transcript rows. Classic retains Sentence/Words/Time.
The native appearance preview now exercises multiple progressive utterances
without using capture or inference. Saved Transcript settings in both lanes,
four visible lines, Never clearing, and gentle animation were restored from the
installed app; the running GPUI window displays the preview in both languages.
Native fullscreen was rechecked with transcript content: all application chrome
and transcript footers are hidden.
The original lane was switched through Rolling lines and Phrase blocks with
sample text visible, then Classic with the saved Time retention. Scrolling up
paused transcript following and exposed Back to live. Rust formatting, both
paging tests, Clippy with warnings denied, and strict bundle signing passed on
the final display implementation.

Reproduce offline worker acceptance with the GPUI runtime's Python and
`scripts/asr_integration_check.py SPEECH_SNAPSHOT --translation TRANSLATION_SNAPSHOT`.
Use the existing snapshots under `~/Library/Application Support/CaptionFlow/models`.
This check synthesizes audio and runs with downloads disabled.
