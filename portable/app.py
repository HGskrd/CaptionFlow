"""CaptionFlow portable desktop: Windows Vulkan/CPU, optional Mac Metal test shell."""
import argparse
import base64
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback
import zipfile

from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QPlainTextEdit, QFileDialog, QSpinBox, QMessageBox, QCheckBox)
from common import ROOT, WorkerSession, doctor, hardware, redact
from runtime_assets import install
from feedback import Feedback, VERSION
from caption_display import CaptionPane, MODES, load_settings
from transcript_scroll import TranscriptScrollLink


class Signals(QObject):
    event = Signal(dict)
    status = Signal(str)
    done = Signal(str)
    failed = Signal(str)


class Window(QMainWindow):
    def __init__(self, data, smoke_test=False):
        super().__init__()
        self.data = Path(data).resolve(); self.data.mkdir(parents=True, exist_ok=True)
        self.feedback = Feedback(self.data)
        self.release = None
        self.signals = Signals()
        self.signals.event.connect(self.receive)
        self.signals.status.connect(self.status)
        self.signals.done.connect(self.done)
        self.signals.failed.connect(self.failure)
        self.cancelled = threading.Event()
        self.task = None; self.session = None; self.closing = False
        self.setWindowTitle("CaptionFlow"); self.resize(1100, 740)
        body = QWidget(); self.setCentralWidget(body); layout = QVBoxLayout(body)
        models = QHBoxLayout(); layout.addLayout(models)
        self.speech = QComboBox()
        for label, identity in (("Nemotron 3.5 · 0.6B · streaming", "nemotron"), ("Phonon 2.5 · compact · CPU chunks", "phonon")):
            self.speech.addItem(label, identity)
        models.addWidget(QLabel("Speech")); models.addWidget(self.speech)
        models.addStretch()
        controls = QHBoxLayout(); layout.addLayout(controls)
        self.backend = QComboBox(); self.backend.addItems(["Vulkan", "CPU"] if os.name == "nt" else ["Metal", "CPU"])
        controls.addWidget(QLabel("Compute / translation")); controls.addWidget(self.backend)
        self.setup = QPushButton("Download / verify models"); self.setup.clicked.connect(self.install_models); controls.addWidget(self.setup)
        self.input = QComboBox()
        for label, mode in (("Microphone + call audio", "mixed"), ("Microphone", "microphone"), ("System audio", "system")):
            if os.name == "nt" or mode == "microphone": self.input.addItem(label, mode)
        controls.addWidget(self.input)
        self.echo = QCheckBox("Speaker echo cancellation"); self.echo.setChecked(True)
        self.echo.setToolTip("Removes speaker leakage from your microphone before mixing. You can turn this off when using headphones.")
        layout.addWidget(self.echo)
        self.source = QComboBox(); self.source.addItems(["French", "English", "Auto"]); controls.addWidget(self.source)
        self.target = QComboBox(); self.target.addItems(["English", "French", "Off"]); controls.addWidget(self.target)
        self.listen = QPushButton("Start listening"); self.listen.clicked.connect(self.start_listening); controls.addWidget(self.listen)
        self.stop = QPushButton("Stop"); self.stop.clicked.connect(self.cancelled.set); self.stop.setEnabled(False); controls.addWidget(self.stop)
        self.display_settings_path = self.data / "display-settings.json"
        self.display_settings = load_settings(self.display_settings_path)
        self.last_speech = float("-inf")
        display = QHBoxLayout(); layout.addLayout(display)
        self.original_style = QComboBox(); self.original_style.addItems(MODES)
        self.translation_style = QComboBox(); self.translation_style.addItems(MODES)
        self.original_style.setCurrentText(self.display_settings["original"])
        self.translation_style.setCurrentText(self.display_settings["translation"])
        self.visible_lines = QComboBox(); self.visible_lines.addItems(["2", "3", "4"])
        self.visible_lines.setCurrentText(str(self.display_settings["lines"]))
        self.clear_after = QComboBox()
        for label, seconds in (("Never", 0), ("5 s", 5), ("10 s", 10)): self.clear_after.addItem(label, seconds)
        self.clear_after.setCurrentIndex(self.clear_after.findData(self.display_settings["clear_after"]))
        for label, control in (("Original", self.original_style), ("Translation", self.translation_style), ("Lines", self.visible_lines), ("Clear after silence", self.clear_after)):
            display.addWidget(QLabel(label)); display.addWidget(control)
            control.currentIndexChanged.connect(self.change_display)
        self.gentle_animation = QCheckBox("Gentle animation"); self.gentle_animation.setChecked(self.display_settings["animation"])
        self.gentle_animation.setToolTip("Respects your system's animation preference.")
        self.gentle_animation.toggled.connect(self.change_display); display.addWidget(self.gentle_animation)
        self.original = CaptionPane(); self.translated = CaptionPane()
        layout.addWidget(self.original, 1); layout.addWidget(self.translated, 1)
        self.original.configure(self.display_settings, "original"); self.translated.configure(self.display_settings, "translation")
        self.transcript_controls = QWidget(); linked_layout = QHBoxLayout(self.transcript_controls)
        linked_layout.setContentsMargins(0, 0, 0, 0)
        linked_layout.addWidget(QLabel("Linked scrolling")); linked_layout.addStretch()
        self.transcript_live = QPushButton("Live"); linked_layout.addWidget(self.transcript_live)
        layout.addWidget(self.transcript_controls)
        self.transcript_link = TranscriptScrollLink([self.original, self.translated], self.update_transcript_controls)
        self.transcript_live.clicked.connect(self.transcript_link.resume)
        self.target.currentIndexChanged.connect(self.configure_transcript_link)
        self.configure_transcript_link()
        self.clear_timer = QTimer(self); self.clear_timer.setInterval(250); self.clear_timer.timeout.connect(self.clear_idle_captions)
        if self.display_settings["clear_after"]: self.clear_timer.start()
        bench = QHBoxLayout(); layout.addLayout(bench)
        self.fixture = QComboBox()
        for label, filename in (("Mixed call · turn-taking", "fr-call-turns.json"), ("Mixed call · overlap", "fr-call-overlap.json"), ("Clean speech", "fr-synthetic.json")):
            self.fixture.addItem(label, filename)
        bench.addWidget(self.fixture)
        self.hours = QComboBox()
        for label, budget in (("Until I stop", None), ("Quick · 5 minutes", 5/60), ("30 minutes", .5), ("Overnight · 8 hours", 8)):
            self.hours.addItem(label, budget)
        bench.addWidget(self.hours)
        self.benchmark = QPushButton("Start / resume benchmark"); self.benchmark.clicked.connect(self.start_benchmark); bench.addWidget(self.benchmark)
        self.export = QPushButton("Export diagnostics / results…"); self.export.clicked.connect(self.export_bundle); bench.addWidget(self.export)
        self.privacy = QPushButton("Privacy / sharing…"); self.privacy.clicked.connect(self.change_consent); bench.addWidget(self.privacy)
        self.update = QPushButton("Update available"); self.update.clicked.connect(self.get_update); self.update.hide(); bench.addWidget(self.update)
        self.fullscreen = QPushButton("Full screen · F11"); self.fullscreen.clicked.connect(self.toggle_fullscreen); bench.addWidget(self.fullscreen)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(200); self.log.setMaximumHeight(140); layout.addWidget(self.log)
        QShortcut(QKeySequence("F11"), self, activated=self.toggle_fullscreen)
        QShortcut(QKeySequence("Escape"), self, activated=self.showNormal)
        self.status("Models stay local. Setup downloads about 2 GB. Benchmarks replay synthetic calls; they do not record you.")
        self.latest_revision = 0; self.latest_caption = None; self.translated_revision = 0
        if not smoke_test:
            QTimer.singleShot(0, self.first_launch)

    def change_display(self):
        self.display_settings = {"original": self.original_style.currentText(), "translation": self.translation_style.currentText(),
                                 "lines": int(self.visible_lines.currentText()), "clear_after": self.clear_after.currentData(),
                                 "animation": self.gentle_animation.isChecked()}
        self.display_settings_path.write_text(json.dumps(self.display_settings), encoding="utf-8")
        self.original.configure(self.display_settings, "original"); self.translated.configure(self.display_settings, "translation")
        if self.display_settings["clear_after"]: self.clear_timer.start()
        else: self.clear_timer.stop()
        self.configure_transcript_link()

    def configure_transcript_link(self):
        self.transcript_link.configure(self.original.mode == "Transcript" and self.translated.mode == "Transcript" and self.target.currentText() != "Off")
        self.update_transcript_controls()

    def update_transcript_controls(self):
        self.transcript_controls.setVisible(self.transcript_link.enabled)
        self.transcript_live.setText("Live" if self.transcript_link.follows_live else "Back to live")
        self.transcript_live.setEnabled(not self.transcript_link.follows_live)

    def clear_idle_captions(self):
        for pane in (self.original, self.translated):
            was_cleared = pane.document.cleared
            pane.document.clear_if_idle(self.display_settings["clear_after"], self.last_speech)
            if was_cleared != pane.document.cleared: pane.refresh()

    def first_launch(self):
        if self.feedback.state.get("consent") is None:
            self.change_consent()
        elif self.feedback.allowed:
            self.sync_feedback()

    def change_consent(self):
        if self.feedback.allowed:
            if QMessageBox.question(self, "Disable sharing?", "Disable all internet access and delete this installation's uploaded reports?", defaultButton=QMessageBox.No) == QMessageBox.Yes:
                try: self.feedback.revoke()
                except Exception as exc: self.status(str(exc))
                self.update.hide(); self.status("Local-only mode. Model files must be supplied offline.")
            return
        prompt = ("Help improve CaptionFlow?\n\nYes enables model downloads, sends hardware specifications, benchmark metrics and error fingerprints to Ishan's Hetzner service, and checks for signed updates. Audio, captions and translations are never uploaded. Reports expire after 30 days.\n\nNo keeps the app local-only: no downloads, uploads or update checks. Supply model files offline. You can change this choice here later.")
        enabled = QMessageBox.question(self, "Share performance data?", prompt, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes
        self.feedback.consent(enabled)
        self.status("Online participation enabled." if enabled else "Local-only mode enabled. No internet access.")
        if enabled: self.sync_feedback()

    def sync_feedback(self):
        if not self.feedback.allowed: return
        def sync():
            try:
                self.feedback.diagnostic(hardware(), "startup")
                release = self.feedback.check_update()
                if release: self.signals.event.emit({"type": "update", "release": release})
            except Exception as exc:
                self.signals.status.emit("Report/update service unavailable; local use continues: " + redact(exc))
        threading.Thread(target=sync, daemon=True).start()

    def get_update(self):
        if not self.release or not self.feedback.allowed: return
        def update():
            path = self.feedback.download_update(self.release)
            self.signals.status.emit(f"Verified update saved to {path}. Extract into a new folder and open Start CaptionFlow.cmd; keep your data folder. The running app was not overwritten.")
        self.launch(update)

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def status(self, message):
        message = redact(message)
        self.log.appendPlainText(message)
        path = self.data / "application.log"
        if path.exists() and path.stat().st_size > 1024 * 1024:
            path.replace(self.data / "application.previous.log")
        with path.open("a", encoding="utf-8") as stream:
            stream.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")

    def config_path(self, backend=None, speech=None):
        backend = backend or self.backend.currentText().lower()
        speech = speech or self.speech.currentData()
        suffix = "" if speech == "nemotron" else "-" + speech
        return self.data / ("configuration-" + backend + suffix + ".json")

    def configuration(self):
        path = self.config_path()
        if not path.exists():
            raise RuntimeError("Download / verify models for the selected compute backend first.")
        config = json.loads(path.read_text(encoding="utf-8"))
        config["source_language"] = {"French": "fr", "English": "en", "Auto": "auto"}[self.source.currentText()]
        config["targets"] = [] if self.target.currentText() == "Off" else [self.target.currentText()]
        return config

    def launch(self, work):
        if self.task and self.task.is_alive(): return
        self.cancelled.clear()
        for widget in (self.setup, self.listen, self.benchmark, self.backend, self.speech, self.input, self.source, self.target, self.hours, self.fixture, self.echo, self.export, self.privacy, self.update):
            widget.setEnabled(False)
        self.stop.setEnabled(True)
        def execute():
            try:
                work()
                self.signals.done.emit("Stopped" if self.cancelled.is_set() else "Finished")
            except Exception as exc:
                self.signals.failed.emit(redact(str(exc)) + "\n" + redact(traceback.format_exc()))
        self.task = threading.Thread(target=execute, daemon=True); self.task.start()

    def done(self, message):
        self.status(message)
        for widget in (self.setup, self.listen, self.benchmark, self.backend, self.speech, self.input, self.source, self.target, self.hours, self.fixture, self.echo, self.export, self.privacy, self.update):
            widget.setEnabled(True)
        self.stop.setEnabled(False)
        self.session = None
        if self.closing:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(50, self.close)

    def failure(self, message):
        self.done("Failed: " + message)
        if self.feedback.allowed:
            def submit():
                try: self.feedback.diagnostic(hardware(), "task_failed", message)
                except Exception as exc: self.signals.status.emit("Diagnostic upload pending: " + redact(exc))
            threading.Thread(target=submit, daemon=True).start()
        # Keep detailed errors in the export rather than requiring a live call.

    def install_models(self):
        backend = self.backend.currentText().lower(); destination = self.config_path()
        speech = self.speech.currentData()
        def setup():
            config = install(self.data, backend, progress=self.signals.status.emit, allow_network=self.feedback.allowed, speech=speech)
            evidence = doctor(config)
            destination.write_text(json.dumps(config, indent=2), encoding="utf-8")
            (self.data / f"doctor-{backend}-{speech}.json").write_text(json.dumps(evidence, indent=2))
            self.signals.status.emit("Verified CUDA-free speech runtime: " + (backend if speech == "nemotron" else speech + " CPU"))
        self.launch(setup)
        # Downloads resume after interruption; stop cannot cancel a blocking HTTP read.
        self.stop.setEnabled(False)

    def start_listening(self):
        try:
            config = self.configuration()
        except Exception as exc:
            self.status(str(exc)); return
        mode = self.input.currentData()
        if mode != "microphone" and os.name != "nt":
            self.status("Use the native macOS app for Mac system audio capture."); return
        self.latest_revision = 0; self.latest_caption = None; self.translated_revision = 0
        self.original.reset(); self.translated.reset(); self.transcript_link.resume(); self.last_speech = float("-inf")
        echo = self.echo.isChecked()
        self.launch(lambda: self.capture(config, mode, echo))

    def capture(self, config, mode, echo=True):
        from audio_pipeline import AudioProcess
        doctor(config)
        self.session = WorkerSession(config, lambda _, event: self.signals.event.emit(event))
        diagnostic = self.data / ("audio-" + str(time.time_ns()) + ".json")
        audio = AudioProcess(mode, diagnostic, echo)
        # Bounded local history; never store captured audio or captions.
        for old in sorted(self.data.glob("audio-*.json"))[:-9]: old.unlink(missing_ok=True)
        try:
            self.session.ready(cancelled=self.cancelled)
            if self.cancelled.is_set(): return
            audio.start()
            self.signals.status.emit("Listening · " + mode + " · one transcription stream")
            sending = [None]; faults = []
            def forward():
                try:
                    last_activity = 0.0
                    while not self.cancelled.is_set():
                        pcm = audio.next(self.cancelled)
                        if pcm is None: break
                        now = time.monotonic()
                        if now-last_activity >= .25:
                            samples = memoryview(pcm).cast("h")[::16]
                            if samples and sum(value*value for value in samples)/len(samples) > (32768*float(config.get("silence_threshold", .008)))**2:
                                self.signals.event.emit({"type": "speech_activity", "at": now}); last_activity = now
                        sending[0] = ("audio", time.monotonic())
                        self.session.send({"type": "audio", "pcm": base64.b64encode(pcm).decode()})
                        sending[0] = None
                    sending[0] = ("stop", time.monotonic())
                    self.session.stop(timeout=5)
                    sending[0] = None
                except Exception as exc: faults.append(exc)
            transport = threading.Thread(target=forward, daemon=True); transport.start()
            while transport.is_alive():
                transport.join(timeout=.1)
                pending = sending[0]
                if pending is not None and ((pending[0] == "audio" and self.cancelled.is_set()) or time.monotonic()-pending[1] > 5):
                    self.session.process.terminate()  # unblock a wedged stdin write, including Stop
                    transport.join(timeout=2)
                    if transport.is_alive():
                        self.session.process.kill(); transport.join(timeout=2)
                    if not self.cancelled.is_set(): raise RuntimeError("Inference audio transport stalled for five seconds")
                    break
            if faults and not self.cancelled.is_set(): raise faults[0]
        finally:
            failed = sys.exc_info()[0] is not None
            audio.close()
            if diagnostic.exists():
                report = json.loads(diagnostic.read_text())
                if report.get("status") != "failed":
                    report["status"] = "failed" if failed else "stopped"
                    if failed: report["failure_code"] = "parent_capture_or_transport_failure"
                    diagnostic.write_text(json.dumps(report, indent=2))
            self.cancelled.set()
            if self.session:
                self.session.close()
                if self.session.process.returncode:
                    self.signals.status.emit("\n".join(self.session.logs)[-8000:])
            if self.feedback.allowed and diagnostic.exists():
                try: self.feedback.audio_diagnostic(json.loads(diagnostic.read_text()))
                except Exception as exc: self.signals.status.emit("Audio report saved locally; upload pending: " + redact(exc))

    def receive(self, event):
        kind = event["type"]
        if kind == "update":
            if self.feedback.allowed:
                self.release = event["release"]; self.update.setText("Update to " + self.release["version"]); self.update.show()
        elif kind == "speech_activity":
            self.last_speech = event["at"]
        elif kind == "source":
            if event["revision"] < self.latest_revision: return
            self.latest_revision = event["revision"]
            if self.latest_caption != event["caption_id"]:
                self.latest_caption = event["caption_id"]; self.translated_revision = -1
            self.original.receive(event)
        elif kind == "translation" and event["caption_id"] == self.latest_caption:
            if event["revision"] >= self.translated_revision:
                self.translated_revision = event["revision"]
                self.translated.receive(event, translated=True)
        elif kind in ("status", "error"):
            self.status(event.get("message", kind))
            if kind == "error": self.cancelled.set()

    def start_benchmark(self):
        from benchmark import run_suite
        backend = self.backend.currentText().lower()
        speech = self.speech.currentData()
        hours = self.hours.currentData()
        fixture = ROOT / "docs/fixtures" / self.fixture.currentData()
        self.status("Preparing benchmark automatically. Stop saves partial results; matching runs resume next time. Failed profiles are skipped.")
        def work():
            paths = []
            profile_dir = self.data / "benchmark-profiles"; profile_dir.mkdir(exist_ok=True)
            # CPU is a separately measured fallback, never disguised as a GPU result.
            backends = [backend, "cpu"] if backend != "cpu" else ["cpu"]
            for candidate in backends:
                if self.cancelled.is_set(): return
                self.signals.status.emit("Preparing " + candidate + " runtime and checking cached models…")
                try:
                    def setup_progress(message):
                        if self.cancelled.is_set(): raise InterruptedError("Setup paused; downloads can resume")
                        self.signals.status.emit(message)
                    config = install(self.data, candidate, progress=setup_progress,
                                     allow_network=self.feedback.allowed, speech=speech)
                    self.config_path(candidate, speech).write_text(json.dumps(config, indent=2))
                except Exception as exc:
                    self.signals.status.emit(f"{candidate} setup failed: {redact(exc)}. Trying the next backend.")
                    continue
                for context in ((3, 0, 6) if speech == "nemotron" else (None,)):
                    for translate in (False, True):
                        profile = {**config, "source_language": "fr", "nemotron_right_context": context,
                                   "targets": ["English"] if translate else []}
                        path = profile_dir / f"{speech}-{candidate}-{context}-{int(translate)}.json"
                        path.write_text(json.dumps(profile, indent=2)); paths.append(str(path))
            if not paths:
                raise RuntimeError("No backend could be prepared. Setup errors were saved in the diagnostic log; offline mode requires cached models and runtimes.")
            output = self.data / ("benchmark-" + backend + ("" if speech == "nemotron" else "-" + speech))
            def progress(message):
                self.signals.status.emit(message)
                if self.feedback.allowed:
                    reports = sorted(output.glob("*/report.json"), key=lambda path: path.stat().st_mtime)
                    if reports:
                        try: self.feedback.upload(json.loads(reports[-1].read_text()), force=False)
                        except Exception as exc: self.signals.status.emit("Report upload pending: " + redact(exc))
            from audio_benchmark import run as audio_check
            evidence = audio_check(self.data / "audio-dsp-benchmark.json")
            self.signals.status.emit(f"Audio mixer check: {evidence['echo_reduction_db']:.1f} dB simulated echo reduction; {evidence['processing_realtime_factor']:.3f}× real-time processing cost")
            if not evidence["passed"]: raise RuntimeError("Audio mixer self-test failed. Export diagnostics.")
            report = run_suite(paths, fixture, output,
                               hours=hours, trial_seconds=60, cancelled=self.cancelled, progress=progress)
            if self.feedback.allowed:
                try: self.feedback.upload(report)
                except Exception as exc: self.signals.status.emit("Saved locally; report upload pending: " + redact(exc))
        self.launch(work)

    def export_bundle(self):
        destination, _ = QFileDialog.getSaveFileName(self, "Export diagnostic bundle", "CaptionFlow-diagnostics.zip", "ZIP (*.zip)")
        if not destination: return
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("hardware.json", json.dumps(hardware(), indent=2))
            for pattern in ("application*.log", "launcher.log", "audio-*.json", "doctor-*.json", "benchmark-*/report.json", "benchmark-*/*/report.json"):
                for path in self.data.glob(pattern):
                    archive.writestr(str(path.relative_to(self.data)), redact(path.read_text(encoding="utf-8")))
            archive.writestr("README.txt", "Local diagnostic export. No captured audio or caption text is logged. Review hardware and error messages before sharing. No automatic upload occurs.\n")
        self.status("Exported diagnostics and benchmark results.")

    def closeEvent(self, event):
        if self.task and self.task.is_alive():
            self.closing = True; self.cancelled.set(); event.ignore()
            self.status("Stopping safely before closing. An active model download must finish or time out.")
        else:
            event.accept()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=str(Path(__file__).parent / "data"))
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()
    application = QApplication(sys.argv[:1]); window = Window(args.data, args.smoke_test); window.show()
    if args.smoke_test:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(500, application.quit)
    sys.exit(application.exec())


if __name__ == "__main__": main()
