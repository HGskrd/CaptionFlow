import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'portable'))
from PySide6.QtWidgets import QApplication
from caption_display import CaptionPane, DEFAULTS
from transcript_scroll import Anchor, Span, TranscriptScrollLink, position_for


class TranscriptScrollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.application = QApplication.instance() or QApplication([])

    def panes(self, missing=()):
        original = CaptionPane(); translated = CaptionPane()
        for pane in (original, translated):
            pane.resize(420, 190); pane.show()
            pane.configure({**DEFAULTS, 'original': 'Transcript'}, 'original')
        for index in range(30):
            original.document.update(f'Source {index}. ' + 'Short words. '*3, str(index), index, True)
            if index not in missing:
                translated.document.update(f'Translation {index}. ' + 'Une traduction beaucoup plus longue. '*5, str(index), index, True)
        original.refresh(); translated.refresh(); self.application.processEvents()
        link = TranscriptScrollLink([original, translated]); link.configure(True)
        self.addCleanup(original.close); self.addCleanup(translated.close)
        return original, translated, link

    def scroll_to(self, pane, identity, fraction=0):
        span = next(span for span in pane.spans if span.caption_id == str(identity))
        pane.transcript.verticalScrollBar().setValue(int(span.start+span.length*fraction))

    def test_bidirectional_passages_not_document_percentages(self):
        source, translation, link = self.panes()
        self.scroll_to(source, 12)
        self.assertFalse(link.follows_live)
        self.assertEqual(source.transcript.firstVisibleBlock().blockNumber(), 12)
        self.assertEqual(translation.transcript.firstVisibleBlock().blockNumber(), 12)
        self.assertEqual(translation.scroll_anchor().caption_id, '12')
        self.assertFalse(source.back.isVisible()); self.assertFalse(translation.back.isVisible())
        self.scroll_to(translation, 7, .5)
        self.assertEqual(source.scroll_anchor().caption_id, '7')
        self.assertEqual(source.transcript.firstVisibleBlock().blockNumber(), 7)
        self.assertEqual(link.anchor.caption_id, '7')

    def test_new_text_does_not_pull_either_pane_away_from_reading(self):
        source, translation, link = self.panes()
        self.scroll_to(source, 10)
        for pane in (source, translation):
            pane.receive({'text': 'A new live sentence.', 'caption_id': '30', 'revision': 30, 'final': True})
            self.assertEqual(pane.scroll_anchor().caption_id, '10')
        source.resume_following()
        self.assertTrue(link.follows_live)
        for pane in (source, translation):
            self.assertEqual(pane.transcript.verticalScrollBar().value(), pane.transcript.verticalScrollBar().maximum())

    def test_missing_translation_catches_up_without_moving_source(self):
        source, translation, link = self.panes(missing=(12,))
        self.scroll_to(source, 12)
        self.assertEqual(link.anchor.caption_id, '12')
        self.assertEqual(translation.scroll_anchor().caption_id, '11')
        self.assertTrue(translation.sync_hint.isVisible())
        # Production snapshots normally arrive in order; insert a late segment
        # into the timeline to exercise the anchor when missing content appears.
        translation.document.update('Late translation twelve. '*5, '12', 31, True)
        translation.document.entries.sort(key=lambda entry: int(entry['id']))
        translation.refresh()
        self.assertEqual(translation.scroll_anchor().caption_id, '12')
        self.assertFalse(translation.sync_hint.isVisible())
        self.assertEqual(source.scroll_anchor().caption_id, '12')

    def test_resize_and_correction_keep_the_shared_passage(self):
        source, translation, link = self.panes()
        self.scroll_to(translation, 14)
        source.resize(680, 190); self.application.processEvents()
        source.document.update('Corrected earlier passage. '*20, '3', 99, True)
        source.refresh()
        self.assertEqual(source.scroll_anchor().caption_id, '14')
        self.assertEqual(translation.scroll_anchor().caption_id, '14')

    def test_single_transcript_stays_independent(self):
        source, translation, link = self.panes()
        translation.configure(DEFAULTS, 'translation'); link.configure(False)
        self.scroll_to(source, 8)
        self.assertFalse(source.follows_live)
        self.assertTrue(translation.follows_live)

    def test_window_links_only_when_both_transcripts_are_enabled(self):
        from app import Window
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            window = Window(directory, smoke_test=True)
            self.addCleanup(window.close)
            window.original_style.setCurrentText('Transcript')
            self.assertFalse(window.transcript_link.enabled)
            window.translation_style.setCurrentText('Transcript')
            self.assertTrue(window.transcript_link.enabled)
            self.assertFalse(window.transcript_controls.isHidden())
            window.target.setCurrentText('Off')
            self.assertFalse(window.transcript_link.enabled)
            self.assertTrue(window.transcript_controls.isHidden())
            window.target.setCurrentText('French')
            self.assertTrue(window.transcript_link.enabled)
            window.original_style.setCurrentText('Rolling lines')
            self.assertFalse(window.transcript_link.enabled)

    def test_mapping_missing_history_and_unicode(self):
        spans = [Span('a', 0, 2), Span('c', 10, 8)]
        self.assertEqual(position_for(Anchor('c', .5), spans, ['a', 'b', 'c']), 14)
        self.assertEqual(position_for(Anchor('b', .5), spans, ['a', 'b', 'c']), 0)
        self.assertEqual(position_for(Anchor('evicted', .8), spans, ['c']), 10)
        source, translation, link = self.panes()
        source.document.update('👩🏽‍💻 日本語\nDeuxième ligne française.', '5', 99, True)
        source.refresh(); self.scroll_to(source, 5)
        self.assertEqual(translation.scroll_anchor().caption_id, '5')


if __name__ == '__main__': unittest.main()
