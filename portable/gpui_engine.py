"""Private JSON-lines adapter from the shipped Windows engine to the GPUI view.

Qt owns existing capture controls and native dialogs; its main window stays hidden.
Audio, model setup, graceful draining, and caption documents use the tested engine.
"""
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import uuid

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QFontMetricsF, QIcon
from PySide6.QtWidgets import QApplication
from app import Window
from caption_display import CaptionPane
from common import WORKER_DIR, default_data_directory, seed_bundled_data
LANGUAGES = json.loads((WORKER_DIR / "languages.json").read_text())
from transcript_scroll import Span, anchor_at, position_for


class Backend(Window):
    def __init__(self, data):
        super().__init__(data, smoke_test=True)
        self.session_id = str(uuid.uuid4())
        self.quitting = False
        self.previewed = False
        self.activity = "idle"
        self.activity_started = time.monotonic()
        self.last_source_at = 0
        self.backlog = 0
        self.notice = "Ready to listen"
        self.failure_message = ""
        self.width = 720
        self.height = 280
        self.positions = {}
        self.layout_cache = {}
        self.translation_panes = {}
        self.speakers = {}
        self.extra_path = self.data / 'gpui-preferences.json'
        try:
            self.extra = json.loads(self.extra_path.read_text())
        except (OSError, ValueError):
            self.extra = {}
        self.extra = {"typeface": "System", "fontSize": 54, "displayMode": "Both",
                      "foreground": {"r": .96, "g": .96, "b": .94, "a": 1},
                      "background": {"r": .065, "g": .075, "b": .085, "a": 1}, **self.extra}
        for key in ('backend', 'speech', 'input', 'source', 'target'):
            if key in self.extra:
                widget = getattr(self, key)
                index = widget.findData(self.extra[key]) if key in ('speech', 'input') else widget.findText(self.extra[key])
                if index >= 0: widget.setCurrentIndex(index)
        self.extra.setdefault('targets', [] if self.target.currentText() == 'Off' else [self.target.currentText()])
        self.refresh_translation_panes()
        self.signals.failed.connect(self.record_failure)
        self.signals.status.connect(self.set_notice)

    def status(self, message):
        super().status(message)
        self.notice = message

    def set_notice(self, message):
        self.notice = message

    def record_failure(self, message):
        self.failure_message = message

    def sync_feedback(self):
        # The GPUI executable owns component updates. Keep the existing consent
        # and opt-in diagnostics; never offer the legacy full-archive updater.
        def sync():
            if self.feedback.allowed:
                try:
                    from common import hardware
                    self.feedback.diagnostic(hardware(), 'startup')
                except Exception:
                    self.signals.status.emit('Feedback service unavailable; local use continues.')
        threading.Thread(target=sync, daemon=True).start()

    def command(self, value):
        kind = value.get('type')
        running = self.task is not None and self.task.is_alive()
        if kind == 'viewport':
            self.width = max(1, min(10000, float(value['width'])))
            self.height = max(1, min(10000, float(value['height'])))
        elif kind in ('shutdown', 'quit'):
            self.quitting = True
            self.stop_processing()
        elif kind == 'stop': self.stop_processing()
        elif kind == 'start' and not running:
            self.failure_message = ''
            if self.feedback.state.get('consent') is None: self.change_consent()
            self.session_id = str(uuid.uuid4()); self.positions.clear()
            self.speakers.clear()
            for pane in self.translation_panes.values(): pane.reset()
            self.activity = 'loading'; self.activity_started = time.monotonic()
            self.start_listening()
        elif kind == 'install' and not running:
            self.activity = 'setup'; self.activity_started = time.monotonic()
            self.install_models()
        elif kind == 'privacy': self.change_consent()
        elif kind == 'dismissNotice': self.notice = ''
        elif kind == 'dismissError': self.failure_message = ''
        elif kind == 'preview' and not running:
            self.previewed = True
            self.session_id = str(uuid.uuid4()); self.positions.clear()
            self.original.reset(); self.translated.reset()
            self.original.receive({'type': 'source', 'caption_id': 'preview', 'revision': 1,
                                   'text': 'Live captions, clear and easy to follow.', 'final': True})
            samples = {'French': 'Des sous-titres en direct, clairs et faciles à suivre.', 'Spanish': 'Subtítulos en directo, claros y fáciles de seguir.', 'German': 'Live-Untertitel, klar und leicht zu verfolgen.'}
            for language, pane in self.translation_panes.items():
                pane.reset()
                self.receive({'type':'translation', 'caption_id':'preview','revision':1,'language':language,
                              'text':samples.get(language, f'{language} · sample translation'), 'final':True})
        elif kind == 'backToLive':
            if self.linked or not value.get('lane'): self.positions.clear()
            else: self.positions.pop(value['lane'], None)
        elif kind == 'scroll':
            lane = value.get('lane', 'original')
            spans = self.layout_cache.get(lane, {}).get('spans', [])
            anchor = anchor_at(max(0, value.get('offset', 0)), spans)
            for key in ['original'] + ['translation:' + lang for lang in self.extra['targets']] if self.linked else (lane,): self.positions[key] = anchor
        elif kind == 'enableDiarization' and not running:
            self.extra['diarizationModel'] = 'nvidia/Nemotron-3-Diarization'
            self.extra_path.write_text(json.dumps(self.extra), encoding='utf-8')
        elif kind == 'clearDiarization' and not running:
            self.extra['diarizationModel'] = ''
            self.extra_path.write_text(json.dumps(self.extra), encoding='utf-8')
        elif kind == 'edit' and value.get('key') == 'diarizationModel' and not running:
            from PySide6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(None, 'Speaker detection model', '', 'Sortformer GGUF (*.gguf)')
            if path:
                self.extra['diarizationModel'] = path
                self.extra_path.write_text(json.dumps(self.extra), encoding='utf-8')
        elif kind == 'edit' and value.get('key') == 'language' and not running:

            from PySide6.QtWidgets import QInputDialog
            language, ok = QInputDialog.getText(None, 'Translation language', 'Language name')
            if ok and language.strip() and len(language.strip()) <= 80:
                self.extra['targets'] = list(dict.fromkeys(self.extra['targets'] + [language.strip()]))
                self.refresh_translation_panes()
                self.extra_path.write_text(json.dumps(self.extra), encoding='utf-8')
        elif kind == 'set':
            key, item = value.get('key'), value.get('value')
            controls = {'backend': self.backend, 'speech': self.speech, 'input': self.input,
                        'sourceLanguage': self.source, 'targetLanguage': self.target,
                        'captionPresentation': self.original_style, 'translationPresentation': self.translation_style,
                        'captionVisibleLines': self.visible_lines, 'captionClearAfter': self.clear_after}
            if key in controls:
                if running and key in ('backend', 'speech', 'input', 'sourceLanguage', 'targetLanguage'): return
                widget = controls[key]
                index = widget.findData(item) if key in ('speech', 'input', 'captionClearAfter') else widget.findText(str(item))
                if index >= 0: widget.setCurrentIndex(index)
            elif key == 'targets' and not running:
                if isinstance(item, list) and all(isinstance(x, str) and 0 < len(x.strip()) <= 80 for x in item):
                    self.extra['targets'] = list(dict.fromkeys(x.strip() for x in item))
                    self.refresh_translation_panes()
            elif key == 'captionGentleAnimation': self.gentle_animation.setChecked(bool(item))
            elif key == 'fontSize': self.extra[key] = min(120, max(24, float(item)))
            elif key in ('typeface', 'displayMode'):
                allowed = ['System', 'Arial', 'Segoe UI', 'Georgia'] if key == 'typeface' else ['Original', 'Translation', 'Both']
                if item in allowed: self.extra[key] = item
            self.extra.update(backend=self.backend.currentText(), speech=self.speech.currentData(),
                              input=self.input.currentData(), source=self.source.currentText(), target=self.target.currentText())
            self.extra_path.write_text(json.dumps(self.extra), encoding='utf-8')

    def refresh_translation_panes(self):
        for language in self.extra['targets']:
            if language not in self.translation_panes: self.translation_panes[language] = CaptionPane()
            self.translation_panes[language].configure(self.display_settings, 'translation')
        for language in list(self.translation_panes):
            if language not in self.extra['targets']:
                self.translation_panes.pop(language).deleteLater()

    def install_models(self):
        from runtime_assets import install
        from common import doctor
        from diarization import resolve_model
        backend = self.backend.currentText().lower(); speech = self.speech.currentData()
        destination = self.config_path()
        def progress(message):
            if self.cancelled.is_set(): raise InterruptedError('Setup stopped')
            self.signals.status.emit(message)
        def setup():
            # The GPUI entry point bypasses the old windowed launcher, so seed
            # its bundled SDK archives here before any offline setup attempt.
            seed_bundled_data(self.data)
            config = install(self.data, backend, progress=progress, allow_network=self.feedback.allowed, speech=speech)
            if speech == 'nemotron' and self.extra.get('diarizationModel'):
                resolve_model(self.extra['diarizationModel'], self.data/'models', self.feedback.allowed, progress)
            doctor(config)
            destination.write_text(json.dumps(config, indent=2), encoding='utf-8')
            self.signals.status.emit('Models and runtime are ready')
        self.launch(setup)

    def configuration(self):
        config = super().configuration()
        config['targets'] = [] if self.extra['displayMode'] == 'Original' else self.extra['targets']
        if self.speech.currentData() == 'nemotron': config['diarization_model'] = self.extra.get('diarizationModel', '')
        return config

    def change_display(self):
        super().change_display()
        if hasattr(self, 'translation_panes'):
            for pane in self.translation_panes.values(): pane.configure(self.display_settings, 'translation')

    def clear_idle_captions(self):
        super().clear_idle_captions()
        for pane in getattr(self, 'translation_panes', {}).values():
            cleared = pane.document.cleared
            pane.document.clear_if_idle(self.display_settings['clear_after'], self.last_speech)
            if cleared != pane.document.cleared: pane.refresh()

    def stop_processing(self):
        self.activity = 'stopping'; self.activity_started = time.monotonic()
        self.cancelled.set()
        if self.session: self.session.cancel()
        self.notice = 'Stopping…'

    def receive(self, event):
        if event['type'] == 'speaker':
            self.speakers[str(event['caption_id'])] = str(event['speaker_id'])
            if len(self.speakers) > 256:
                retained = {str(entry['id']) for entry in self.original.document.entries}
                self.speakers = {key:value for key,value in self.speakers.items() if key in retained}
        elif event['type'] == 'translation':
            pane = self.translation_panes.get(event.get('language'))
            if pane: pane.receive(event, translated=True)
        else: super().receive(event)
        if event['type'] == 'ready':
            self.activity = 'listening'; self.activity_started = time.monotonic()
        elif event['type'] == 'source':
            self.last_source_at = time.monotonic()
            self.backlog = event.get('backlog_seconds', 0)

    @property
    def linked(self):
        return self.original.mode == self.translated.mode == 'Transcript' and bool(self.extra['targets']) and self.extra['displayMode'] == 'Both'

    def lane(self, pane, key):
        font = QFont('Segoe UI' if self.extra['typeface'] == 'System' else self.extra['typeface'])
        font.setPixelSize(int(self.extra['fontSize']))
        metrics = QFontMetricsF(font)
        line_height = metrics.height() + 5
        tokens = pane.document.tokens(pane.mode)
        by_token = {token.id: entry['id'] for entry in pane.document.entries for token in entry['tokens']}
        signature = (tuple((w.id, w.text) for w in tokens), self.width, self.extra['fontSize'], self.extra['typeface'], pane.mode)
        cached = self.layout_cache.get(key, {})
        if cached.get('signature') != signature:
            rows, words, used = [], [], 0
            def flush():
                nonlocal words, used
                if words:
                    rows.append({'id': words[0]['id'], 'captionID': words[0]['captionID'], 'words': words,
                                 'text': ''.join(w['text'] for w in words)})
                words, used = [], 0
            for token in tokens:
                text = token.text.replace('\n', ' ')
                parts = [text]
                if metrics.horizontalAdvance(text) > self.width:
                    parts, part = [], ''
                    for character in text:
                        if part and metrics.horizontalAdvance(part + character) > self.width:
                            parts.append(part); part = ''
                        part += character
                    if part: parts.append(part)
                for index, part in enumerate(parts):
                    if words and used + metrics.horizontalAdvance(part.rstrip()) > self.width: flush()
                    width = metrics.horizontalAdvance(part)
                    words.append({'id': f'{token.id}:{index}', 'captionID': by_token.get(token.id, ''),
                                  'text': part, 'width': width, 'entering': False})
                    used += width
                if '\n' in token.text: flush()
            flush()
            spans = []
            for index, row in enumerate(rows):
                if spans and spans[-1].caption_id == row['captionID']:
                    previous = spans.pop(); spans.append(Span(previous.caption_id, previous.start, previous.length + line_height))
                else: spans.append(Span(row['captionID'], index * line_height, line_height))
            cached = self.layout_cache[key] = {'signature': signature, 'rows': rows, 'spans': spans}
        rows = cached['rows']
        capacity = max(1, min(self.display_settings['lines'], int(self.height / line_height)))
        if pane.mode == 'Rolling lines': rows = rows[-capacity:]
        elif pane.mode == 'Phrase blocks': rows = rows[max(0, (len(rows) - 1) // capacity * capacity):]
        timeline = [entry['id'] for entry in self.original.document.entries]
        anchor = self.positions.get(key)
        offset = position_for(anchor, cached['spans'], timeline) if anchor else 0
        rows = [{**row, 'speaker_id': self.speakers.get(str(row['captionID']), '')} for row in rows]
        return {'id': key, 'mode': pane.mode, 'rows': rows, 'capacity': capacity, 'offset': offset or 0,
                'followsLive': key not in self.positions, 'pending': False}, line_height

    def snapshot(self):
        running = self.task is not None and self.task.is_alive()
        settings = self.display_settings
        elapsed = int(time.monotonic() - self.activity_started)
        phase = self.notice
        if running and self.activity in ('loading', 'setup'):
            phase = f'{phase} · {elapsed}s elapsed'
        elif running and self.activity == 'listening' and self.backlog > 2:
            phase = f'Catching up · {self.backlog:.1f}s behind · select translation Off or a faster model if this grows'
        elif running and self.activity == 'listening' and self.last_source_at and time.monotonic() - self.last_source_at > 8:
            phase = 'Listening · waiting for speech or processing the next caption'
        def field(key, label, value, choices=None, kind='select', **extra):
            return {'kind': kind, 'key': key, 'label': label, 'value': value,
                    'options': [{'value': a, 'label': b} for a, b in (choices or [])], **extra}
        def choices(widget):
            return [(widget.itemData(i) if widget in (self.speech, self.input) else widget.itemText(i), widget.itemText(i)) for i in range(widget.count())]
        def section(title, fields): return {'title': title, 'fields': fields, 'note': ''}
        modes = [(x, x) for x in ('Rolling lines', 'Phrase blocks', 'Transcript')]
        style = [section('Appearance', [field('typeface', 'Typeface', self.extra['typeface'], [(x,x) for x in ('System','Arial','Segoe UI','Georgia')]),
                  field('fontSize', 'Font size', self.extra['fontSize'], kind='number', min=24, max=120, step=2)]),
                 section('Caption display', [field('captionPresentation', 'Original', settings['original'], modes),
                    field('translationPresentation', 'Translation', settings['translation'], modes),
                    field('captionVisibleLines', 'Visible lines', settings['lines'], [(x,str(x)) for x in (2,3,4)]),
                    field('captionClearAfter', 'Clear after silence', settings['clear_after'], [(0,'Never'),(5,'5 seconds'),(10,'10 seconds')]),
                    field('captionGentleAnimation', 'Gentle animation', settings['animation'], kind='toggle')])]
        language = [section('Listening', [field('input','Audio',self.input.currentData(),choices(self.input),disabled=running),
                     field('sourceLanguage','Spoken language',self.source.currentText(),choices(self.source),disabled=running)]),
                    section('On screen', [field('displayMode','Show',self.extra['displayMode'],[(x,x) for x in ('Original','Translation','Both')]),
                     field('targetsSummary', 'Selected targets', ', '.join(self.extra['targets']) or 'Translation off', kind='status')]),
                    section('Translate into', [field(lang,lang,lang in self.extra['targets'],kind='target',disabled=running) for lang in sorted(set(LANGUAGES+self.extra['targets']))] + [field('language','Add another language…','',kind='edit',disabled=running)])]
        models = [section('Models', [field('speech','Speech',self.speech.currentData(),choices(self.speech),disabled=running),
                   field('backend','Compute / translation',self.backend.currentText(),choices(self.backend),disabled=running),
                   field('install','Download / verify models','',kind='action',disabled=running),
                   field('enableDiarization','Enable speaker detection (~107 MB)','',kind='action',disabled=running or self.speech.currentData()!='nemotron' or bool(self.extra.get('diarizationModel'))),
                   field('diarizationModel','Speaker detection model…',Path(self.extra.get('diarizationModel','')).name or 'None',kind='edit',disabled=running or self.speech.currentData()!='nemotron'),
                   field('clearDiarization','Disable speaker detection','',kind='action',disabled=running or not self.extra.get('diarizationModel'))])]
        lanes, line_height = [], self.extra['fontSize'] * 1.3
        for pane, key in [(self.original, 'original')] + [(pane, 'translation:' + lang) for lang, pane in self.translation_panes.items()]:
            if key == 'original' and self.extra['displayMode'] == 'Translation': continue
            if key.startswith('translation:') and self.extra['displayMode'] == 'Original': continue
            lane, line_height = self.lane(pane,key); lanes.append(lane)
        return {'preferences': self.extra, 'sections': [style,language,models],
                'canvas': {'session': self.session_id, 'lanes': lanes, 'lineHeight': line_height,
                           'fontFamily': 'Segoe UI' if self.extra['typeface'] == 'System' else self.extra['typeface'],
                           'systemFont': self.extra['typeface'] == 'System', 'linked': self.linked,
                           'gentleAnimation': settings['animation']},
                'original': ''.join(w.text for w in self.original.document.tokens('Transcript')),
                'translations': {lang: ''.join(w.text for w in pane.document.tokens('Transcript')) for lang,pane in self.translation_panes.items()},
                'active': running, 'listening': running and self.activity == 'listening', 'stopping': running and self.cancelled.is_set(),
                'busy': running and self.activity == 'loading', 'installing': running and self.activity == 'setup',
                'installed': self.config_path().exists(), 'online': self.feedback.allowed,
                'phase': phase, 'inputStatus': phase, 'audioState': 'Waiting for speech…',
                'notice': self.notice, 'error': self.failure_message, 'shutdownComplete': self.quitting and not running}


def main():
    wire = sys.stdout
    wire.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout = sys.stderr
    data = default_data_directory() if sys.platform.startswith('linux') else Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'CaptionFlow'
    app = QApplication(sys.argv[:1]); app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(str(Path(__file__).parent / 'resources/CaptionFlow.ico')))
    backend = Backend(data)
    commands = queue.Queue(maxsize=128)
    def read():
        for line in sys.stdin:
            try: commands.put(json.loads(line))
            except ValueError: pass
        commands.put({'type': 'shutdown'})
    threading.Thread(target=read, daemon=True).start()
    previous = None
    def tick():
        nonlocal previous
        for _ in range(32):
            try: backend.command(commands.get_nowait())
            except queue.Empty: break
            except Exception as exc: backend.failure_message = str(exc)
        state = backend.snapshot()
        encoded = json.dumps(state, ensure_ascii=False, separators=(',', ':'))
        if encoded != previous:
            wire.write(encoded + '\n'); wire.flush(); previous = encoded
        if state['shutdownComplete']:
            evidence = os.environ.get('CAPTIONFLOW_SMOKE_EVIDENCE')
            if evidence:
                Path(evidence).write_text(json.dumps({'preview': backend.previewed,
                    'original_characters': len(state['original']), 'shutdown_received': True}), encoding='utf-8')
            app.quit()
    timer = QTimer(); timer.timeout.connect(tick); timer.start(33)
    app.exec()


if __name__ == '__main__': main()
