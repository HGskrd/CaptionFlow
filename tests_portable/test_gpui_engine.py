import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'portable'))
from PySide6.QtWidgets import QApplication
from gpui_engine import Backend, LANGUAGES
from common import WorkerSession

APP = QApplication.instance() or QApplication([])


class GPUIBackendTests(unittest.TestCase):
    def test_multiple_languages_reach_worker_and_independent_visible_lanes(self):
        with tempfile.TemporaryDirectory() as directory:
            backend=Backend(directory)
            backend.command({'type':'set','key':'targets','value':['French','Spanish','Japanese','French']})
            backend.config_path().write_text('{}')
            self.assertEqual(backend.configuration()['targets'], ['French','Spanish','Japanese'])
            for language,text in [('French','Bonjour le monde.'),('Spanish','Hola al mundo.'),('Japanese','こんにちは世界。')]:
                backend.receive(dict(type='translation', language=language, caption_id='a', revision=1, text=text, final=True))
            backend.receive(dict(type='speaker',caption_id='a',speaker_id='2'))
            state=backend.snapshot()
            for lane in state['canvas']['lanes'][1:]:
                self.assertEqual(lane['rows'][0]['speaker_id'],'2')
            self.assertEqual([lane['id'] for lane in state['canvas']['lanes']], ['original','translation:French','translation:Spanish','translation:Japanese'])
            self.assertEqual(state['translations']['Spanish'],'Hola al mundo.')
            self.assertEqual(state['translations']['Japanese'],'こんにちは世界。')
            self.assertFalse(state['online'])
            self.assertIn('Czech',LANGUAGES)
            backend.deleteLater();APP.processEvents()

    def test_loading_is_distinct_from_listening_and_persists_selected_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            backend=Backend(directory)
            backend.command({'type':'set','key':'targets','value':['German','Hindi']})
            backend.deleteLater();APP.processEvents()
            backend=Backend(directory)
            self.assertEqual(backend.extra['targets'],['German','Hindi'])
            class Running:
                def is_alive(self): return True
            backend.task=Running();backend.activity='loading';backend.activity_started=time.monotonic()-8
            state=backend.snapshot()
            self.assertTrue(state['busy']);self.assertFalse(state['listening']);self.assertIn('8s elapsed',state['phase'])
            backend.task=None;backend.deleteLater();APP.processEvents()

    def test_diarization_leaves_ambiguous_and_unlabelled_speech_neutral(self):
        from diarization import dominant_speaker
        self.assertIsNone(dominant_speaker(0,2,[]))
        self.assertEqual(dominant_speaker(0,2,[(0,2,1)]),'1')
        self.assertIsNone(dominant_speaker(0,2,[(0,1,1),(1,2,2)]))
        self.assertEqual(dominant_speaker(0,2,[(0,1.8,2),(1.8,2,1)]),'2')
        from diarization import LiveDiarization
        tracker=object.__new__(LiveDiarization)
        tracker.ranges={'a':(0,2)};tracker.labels={};tracker.segments=[(0,2,1)]
        events=[];tracker.emit=lambda kind,**value: events.append(value)
        tracker.label_turns()
        tracker.segments=[(0,1,1),(1,2,2)];tracker.label_turns()
        self.assertEqual([event['speaker_id'] for event in events],['1',''])

    def test_stop_interrupts_a_worker_that_cannot_read_stdin(self):
        process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
        session=object.__new__(WorkerSession);session.process=process
        started=time.monotonic()
        try:
            session.cancel();process.wait(timeout=2)
            self.assertLess(time.monotonic()-started,2)
        finally:
            if process.poll() is None: process.kill();process.wait()


if __name__=='__main__': unittest.main()
