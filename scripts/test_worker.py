import json
import pathlib
import sys
import tempfile
import threading
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'Sources/CaptionFlow/Resources/worker'))
from caption_core import cache_reuse, latest_caption, visible_words

class CaptionsTest(unittest.TestCase):
    def test_sentence_rollover(self):
        self.assertEqual(latest_caption('First sentence. Here is the next one'), 'Here is the next one')
        self.assertEqual(latest_caption('First sentence. '), 'First sentence.')
        self.assertEqual(latest_caption('你好。世界！'), '世界！')
        self.assertLessEqual(len(latest_caption('word ' * 500).split()), 33)
        self.assertLessEqual(len(latest_caption('字' * 500)), 221)

    def test_prefix_revision_and_cache_bounds(self):
        self.assertEqual(cache_reuse([1, 2, 3, 4], [1, 2, 8, 9], 20), 2)
        self.assertEqual(cache_reuse([1, 2, 3], [1, 2, 3, 4], 10), 3)
        self.assertEqual(cache_reuse([1, 2, 3], [1, 2, 3], 10), 2)
        self.assertEqual(cache_reuse([1, 2, 3], [1, 2, 3, 4], 1), 1)
        self.assertEqual(cache_reuse([1, 2], [9, 8], 2), 0)

    def test_streams_whole_words(self):
        self.assertEqual(visible_words('Bon'), '')
        self.assertEqual(visible_words('Bonjour le mon'), 'Bonjour le')
        self.assertEqual(visible_words('Bonjour le monde', final=True), 'Bonjour le monde')
        self.assertEqual(visible_words('你好'), '你好')

    def test_audio_queue_coalesces_and_closes_utterances(self):
        import numpy as np
        from worker import AudioInbox
        inbox = AudioInbox(0.008)
        voice = np.full(3200, 0.1, dtype='<f4').tobytes()
        silence = np.zeros(3200, dtype='<f4').tobytes()
        for _ in range(12): inbox.feed(voice)
        self.assertEqual(len(inbox.jobs), 1)
        for _ in range(4): inbox.feed(silence)
        self.assertTrue(inbox.jobs[-1][2])
        self.assertEqual(inbox.utterance, 1)
        for _ in range(60): inbox.feed(silence)
        self.assertEqual(inbox.utterance, 1)
        for _ in range(200): inbox.feed(voice)
        self.assertLessEqual(len(inbox.jobs), 3)
        self.assertLess(inbox.size, 192000)

    def test_speech_engine_detection(self):
        from worker import speech_engine
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            cases = [
                ({"n_mels": 80, "n_audio_ctx": 1500}, "whisper"),
                ({"target": "nemo.collections.asr.models.EncDecRNNTBPEModel"}, "parakeet"),
                ({"model_type": "voxtral_realtime"}, "voxtral_realtime"),
            ]
            for index, (config, expected) in enumerate(cases):
                model = root / str(index)
                model.mkdir()
                (model / "config.json").write_text(json.dumps(config))
                self.assertEqual(speech_engine(model), expected)

    def test_translation_queue_coalesces_partial_revisions_but_keeps_final(self):
        from worker import TranslationDispatcher

        class Translator:
            def __init__(self):
                self.calls = []
                self.started = threading.Event()
                self.release = threading.Event()

            def translate(self, text, language, source_language, revision, caption_id):
                self.calls.append((text, revision, caption_id))
                if revision == 1:
                    self.started.set()
                    self.release.wait(timeout=2)

        translator = Translator()
        queue = TranslationDispatcher("unused", ["English"], translator_factory=lambda _: translator)
        queue.submit(7, "first", "fr", 1, False)
        self.assertTrue(translator.started.wait(timeout=2))
        queue.submit(7, "second", "fr", 2, False)
        queue.submit(7, "third", "fr", 3, False)
        queue.submit(7, "final", "fr", 4, True)
        translator.release.set()
        queue.finish()
        self.assertEqual(translator.calls, [("first", 1, "7"), ("final", 4, "7")])

if __name__ == '__main__': unittest.main()
