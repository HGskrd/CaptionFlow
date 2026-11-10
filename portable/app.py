"""CaptionFlow portable desktop: Windows Vulkan/CPU, optional Mac Metal test shell."""
import argparse
import base64
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import traceback
import zipfile

from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QPlainTextEdit, QFileDialog, QSpinBox, QMessageBox)
from common import ROOT, WorkerSession, doctor, hardware, redact
from runtime_assets import install
from feedback import Feedback, VERSION


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
        controls = QHBoxLayout(); layout.addLayout(controls)
        self.backend = QComboBox(); self.backend.addItems(["Vulkan", "CPU"] if os.name == "nt" else ["Metal", "CPU"])
        controls.addWidget(QLabel("Compute")); controls.addWidget(self.backend)
        self.setup = QPushButton("Download / verify models"); self.setup.clicked.connect(self.install_models); controls.addWidget(self.setup)
        self.input = QComboBox(); self.input.addItems(["Default microphone", "System audio (Windows)"])
        controls.addWidget(self.input)
        self.source = QComboBox(); self.source.addItems(["French", "English", "Auto"]); controls.addWidget(self.source)
        self.target = QComboBox(); self.target.addItems(["English", "French", "Off"]); controls.addWidget(self.target)
        self.listen = QPushButton("Start listening"); self.listen.clicked.connect(self.start_listening); controls.addWidget(self.listen)
        self.stop = QPushButton("Stop"); self.stop.clicked.connect(self.cancelled.set); self.stop.setEnabled(False); controls.addWidget(self.stop)
        self.original = QLabel("Ready when you are."); self.original.setWordWrap(True); self.original.setAlignment(Qt.AlignCenter)
        self.translated = QLabel(""); self.translated.setWordWrap(True); self.translated.setAlignment(Qt.AlignCenter)
        for label in (self.original, self.translated):
            font = QApplication.font(); font.setPointSize(28)
            label.setFont(font); label.setTextFormat(Qt.PlainText)
            label.setMargin(25); layout.addWidget(label, 1)
        bench = QHBoxLayout(); layout.addLayout(bench)
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
        self.status("Models stay local. Setup downloads about 2 GB. Benchmarks replay the bundled synthetic French fixture; they do not record you.")
        self.latest_revision = 0; self.latest_caption = None; self.translated_revision = 0
        if not smoke_test:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self.first_launch)

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

    def config_path(self):
        return self.data / ("configuration-" + self.backend.currentText().lower() + ".json")

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
        for widget in (self.setup, self.listen, self.benchmark, self.backend, self.input, self.source, self.target, self.hours, self.export, self.privacy, self.update):
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
        for widget in (self.setup, self.listen, self.benchmark, self.backend, self.input, self.source, self.target, self.hours, self.export, self.privacy, self.update):
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
        def setup():
            config = install(self.data, backend, progress=self.signals.status.emit, allow_network=self.feedback.allowed)
            evidence = doctor(config)
            destination.write_text(json.dumps(config, indent=2), encoding="utf-8")
            (self.data / f"doctor-{backend}.json").write_text(json.dumps(evidence, indent=2))
            self.signals.status.emit("Verified CUDA-free speech runtime: " + backend)
        self.launch(setup)
        # Downloads resume after interruption; stop cannot cancel a blocking HTTP read.
        self.stop.setEnabled(False)

    def start_listening(self):
        try:
            config = self.configuration()
        except Exception as exc:
            self.status(str(exc)); return
        loopback = self.input.currentIndex() == 1
        if loopback and os.name != "nt":
            self.status("Use the native macOS app for Mac system audio capture."); return
        self.latest_revision = 0; self.latest_caption = None; self.translated_revision = 0
        self.launch(lambda: self.capture(config, loopback))

    def capture(self, config, loopback):
        import numpy as np
        import soundcard as sc
        doctor(config)
        self.session = WorkerSession(config, lambda _, event: self.signals.event.emit(event))
        audio = queue.Queue(maxsize=50)  # Five seconds, never silently drop frames.
        capture_error = []
        sender_error = []
        try:
            self.session.ready(cancelled=self.cancelled)
            device = sc.get_microphone(sc.default_speaker().id, include_loopback=True) if loopback else sc.default_microphone()
            if device is None: raise RuntimeError("No default audio input is available")
            self.signals.status.emit("Listening · " + ("system audio" if loopback else "microphone"))
            def sender():
                try:
                    while not self.cancelled.is_set() or not audio.empty():
                        try: pcm = audio.get(timeout=.2)
                        except queue.Empty: continue
                        self.session.send({"type": "audio", "pcm": base64.b64encode(pcm).decode()})
                except Exception as exc:
                    sender_error.append(str(exc)); self.cancelled.set()
            transport = threading.Thread(target=sender, daemon=True); transport.start()
            try:
                # Capture all native channels: SoundCard documents corrupt mono WASAPI capture.
                # WASAPI/CoreAudio resamples to 16 kHz; average channels after capture.
                with device.recorder(samplerate=16000, blocksize=1600) as recorder:
                    while not self.cancelled.is_set():
                        frame = recorder.record(numframes=1600)
                        pcm = np.mean(frame, axis=1).astype("<f4").tobytes()
                        try: audio.put_nowait(pcm)
                        except queue.Full:
                            raise RuntimeError("Audio transport is five seconds behind; stopped without silently dropping speech")
                        if self.session.process.poll() is not None:
                            raise RuntimeError("Inference worker stopped. Export diagnostics for details.")
            except Exception as exc:
                capture_error.append(str(exc))
            finally:
                self.cancelled.set(); transport.join(timeout=5)
                if transport.is_alive():
                    self.session.close(); transport.join(timeout=3)
            if capture_error or sender_error: raise RuntimeError("; ".join(capture_error + sender_error))
            self.session.stop(timeout=30)
        finally:
            self.cancelled.set()
            if self.session:
                self.session.close()
                if self.session.process.returncode:
                    self.signals.status.emit("\n".join(self.session.logs)[-8000:])

    def receive(self, event):
        kind = event["type"]
        if kind == "update":
            if self.feedback.allowed:
                self.release = event["release"]; self.update.setText("Update to " + self.release["version"]); self.update.show()
        elif kind == "source":
            self.latest_revision = event["revision"]
            if self.latest_caption != event["caption_id"]:
                self.translated.setText(""); self.latest_caption = event["caption_id"]
            self.original.setText(event["text"])
        elif kind == "translation" and event["caption_id"] == self.latest_caption:
            # A newer source revision may arrive while its predecessor translates.
            if event["revision"] >= getattr(self, "translated_revision", 0):
                self.translated_revision = event["revision"]
                self.translated.setText(event["text"])
        elif kind in ("status", "error"):
            self.status(event.get("message", kind))
            if kind == "error": self.cancelled.set()

    def start_benchmark(self):
        from benchmark import run_suite
        backend = self.backend.currentText().lower()
        hours = self.hours.currentData()
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
                                     allow_network=self.feedback.allowed)
                    (self.data / f"configuration-{candidate}.json").write_text(json.dumps(config, indent=2))
                except Exception as exc:
                    self.signals.status.emit(f"{candidate} setup failed: {redact(exc)}. Trying the next backend.")
                    continue
                for context in (3, 0, 6):
                    for translate in (False, True):
                        profile = {**config, "source_language": "fr", "nemotron_right_context": context,
                                   "targets": ["English"] if translate else []}
                        path = profile_dir / f"{candidate}-{context}-{int(translate)}.json"
                        path.write_text(json.dumps(profile, indent=2)); paths.append(str(path))
            if not paths:
                raise RuntimeError("No backend could be prepared. Setup errors were saved in the diagnostic log; offline mode requires cached models and runtimes.")
            output = self.data / ("benchmark-" + backend)
            def progress(message):
                self.signals.status.emit(message)
                if self.feedback.allowed:
                    reports = sorted(output.glob("*/report.json"), key=lambda path: path.stat().st_mtime)
                    if reports:
                        try: self.feedback.upload(json.loads(reports[-1].read_text()), force=False)
                        except Exception as exc: self.signals.status.emit("Report upload pending: " + redact(exc))
            report = run_suite(paths, ROOT / "docs/fixtures/fr-synthetic.json", output,
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
            for pattern in ("application*.log", "doctor-*.json", "benchmark-*/report.json", "benchmark-*/*/report.json"):
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
