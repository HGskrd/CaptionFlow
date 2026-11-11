import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"portable"))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter
from PySide6.QtCore import QPoint
from caption_display import CaptionDocument, CaptionPane, RollingCanvas, Token, DEFAULTS, load_settings

class CaptionDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.application = QApplication.instance() or QApplication([])

    def test_corrections_and_phrase_replacement(self):
        doc = CaptionDocument()
        doc.update("Send 15 euros tomorrow.", "a", 1, True)
        last_id = doc.tokens("Rolling lines")[-1].id
        doc.update("Send 50 euros tomorrow.", "a", 2, True)
        self.assertEqual(doc.tokens("Rolling lines")[-1].id, last_id)
        self.assertEqual("".join(t.text for t in doc.phrase), "Send 50 euros tomorrow.")
        doc.update("Wrong", "a", 1, True)
        doc.update("Envoyez", "a", 3, hold_revisions=True)
        self.assertEqual("".join(t.text for t in doc.phrase), "Send 50 euros tomorrow.")
        doc.update("Envoyez 50 euros demain.", "a", 3, True, hold_revisions=True)
        self.assertEqual("".join(t.text for t in doc.phrase), "Envoyez 50 euros demain.")

    def test_correcting_first_word_and_extending_preserves_read_word_ids(self):
        doc = CaptionDocument()
        doc.update("He sent fifteen euros to Paris", "a", 0)
        previous = [word.id for word in doc.tokens("Rolling lines")]
        doc.update("She sent fifty euros to Paris yesterday.", "a", 1, True)
        self.assertEqual([word.id for word in doc.tokens("Rolling lines")[:len(previous)]], previous)

    def test_silence_clears_whole_caption_without_erasing_scrollback(self):
        doc = CaptionDocument(); doc.update("One two three.", "a", 0, True, now=100)
        doc.clear_if_idle(5, 104, now=106); self.assertTrue(doc.tokens("Rolling lines"))
        doc.clear_if_idle(5, 100, now=106); self.assertFalse(doc.tokens("Rolling lines"))
        self.assertTrue(doc.tokens("Transcript"))
        doc.update("New speech.", "b", 1, True, now=107)
        self.assertEqual("".join(word.text for word in doc.tokens("Rolling lines")), "New speech.")

    def test_bounded_memory(self):
        doc = CaptionDocument()
        for index in range(250): doc.update(f"Sentence {index}.", str(index), index, True)
        self.assertEqual(len(doc.entries), 200)
        self.assertEqual(doc.entries[-1]["id"], "249")

    def test_rollup_keeps_line_starts_and_renders_unicode(self):
        canvas = RollingCanvas(); canvas.resize(330, 300); canvas.animate = False
        tokens = [Token(i, "word ") for i in range(9)]
        canvas.set_tokens(tokens[:5]); starts = [row[0][0] for row in canvas.rows]
        canvas.set_tokens(tokens)
        self.assertEqual([row[0][0] for row in canvas.rows[:len(starts)]], starts)
        self.assertEqual(len(canvas.visible), 3)
        canvas.set_tokens([Token(20, "👩🏽‍💻"*10), Token(21, " 日本語 العربية français")])
        image = QImage(canvas.size(), QImage.Format_ARGB32); image.fill(0)
        painter = QPainter(image); canvas.render(painter, QPoint()); painter.end()
        self.assertTrue(canvas.visible)
        self.assertFalse(canvas.timer.isActive())
        canvas.close()

    def test_transcript_pause_and_return_to_live(self):
        pane = CaptionPane(); pane.resize(400, 220); pane.show()
        pane.configure({**DEFAULTS, "original": "Transcript"}, "original")
        for index in range(30): pane.receive({"text": f"A complete sentence {index}.", "caption_id": str(index), "revision": index, "final": True})
        self.application.processEvents()
        pane.pause_following(); scrollbar = pane.transcript.verticalScrollBar(); scrollbar.setValue(0)
        pane.receive({"text": "Another live sentence.", "caption_id": "30", "revision": 30, "final": True})
        self.assertEqual(scrollbar.value(), 0)
        self.assertFalse(pane.follows_live)
        pane.resume_following(); self.assertEqual(scrollbar.value(), scrollbar.maximum())
        pane.close()

    def test_window_settings_persist_and_source_updates_have_no_word_queue(self):
        from app import Window
        with tempfile.TemporaryDirectory() as directory, patch("urllib.request.urlopen") as network:
            window = Window(directory, smoke_test=True)
            window.original_style.setCurrentText("Transcript"); window.visible_lines.setCurrentText("4")
            window.clear_after.setCurrentIndex(window.clear_after.findData(10)); window.gentle_animation.setChecked(False)
            event = {"type": "source", "text": "All of these words arrive in one batch.", "caption_id": "a", "revision": 0, "final": True}
            window.receive(event)
            self.assertEqual(window.original.transcript.toPlainText(), event["text"])
            window.close()
            reopened = Window(directory, smoke_test=True)
            self.assertEqual(reopened.original_style.currentText(), "Transcript")
            self.assertEqual(reopened.visible_lines.currentText(), "4")
            self.assertEqual(reopened.clear_after.currentData(), 10)
            self.assertFalse(reopened.gentle_animation.isChecked())
            network.assert_not_called(); reopened.close()

    def test_corrupt_settings_fall_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"settings.json"; path.write_text('{"lines": 999, "original": "bad"}')
            self.assertEqual(load_settings(path), DEFAULTS)

if __name__ == "__main__": unittest.main()
