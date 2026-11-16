import json
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'Sources/CaptionFlow/Resources/worker'))
from caption_core import cache_reuse, visible_words

class CaptionsTest(unittest.TestCase):
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

    def test_hy_mt_uses_its_documented_user_only_prompt(self):
        from worker import translation_messages
        messages = translation_messages("hunyuan_v1_dense", "Bonjour", "English")
        self.assertEqual([message["role"] for message in messages], ["user"])
        self.assertIn("Translate the following text into English", messages[0]["content"])
        self.assertTrue(messages[0]["content"].endswith("\n\nBonjour"))
        self.assertEqual([message["role"] for message in translation_messages("qwen2", "Bonjour", "English")], ["system", "user"])

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
        with self.assertRaisesRegex(RuntimeError, "25 seconds"):
            for _ in range(200): inbox.feed(voice)
        self.assertLessEqual(inbox.queued_samples, 25 * 16000)
        self.assertLess(inbox.size, 192000)

    def test_snapshot_backlog_retains_finals_before_newer_partials(self):
        import numpy as np
        from worker import AudioInbox
        inbox = AudioInbox(0.008)
        voice = np.full(3200, 0.1, dtype='<f4').tobytes()
        silence = np.zeros(3200, dtype='<f4').tobytes()
        for _ in range(4):
            for _ in range(3): inbox.feed(voice)
            for _ in range(4): inbox.feed(silence)
        for _ in range(6): inbox.feed(voice)
        inbox.closed = True
        jobs = []
        while (job := inbox.next()) is not None:
            jobs.append(job)
        self.assertEqual([(u, final) for u, _, final in jobs],
                         [(0, True), (1, True), (2, True), (3, True), (4, False)])
        self.assertEqual(len(jobs[-1][1]), 6 * 3200)
        self.assertEqual(inbox.queued_samples, 0)

    def test_snapshot_unchanged_text_still_emits_final_for_every_engine(self):
        import numpy as np
        from worker import run_snapshot_speech
        for engine in ('whisper', 'parakeet', 'qwen3_asr'):
            with self.subTest(engine=engine):
                samples = np.ones(16000, dtype=np.float32)
                inbox = MagicMock()
                inbox.next.side_effect = [(0, samples, False), (0, samples, False),
                                          (0, samples, True), (1, samples, True), None]
                whisper = MagicMock()
                whisper.transcribe.return_value = {'segments': [{'text': 'Hello world'}], 'language': 'en'}
                model = MagicMock()
                model.generate.return_value.text = 'Hello world'
                model.generate.return_value.language = 'en'
                mlx = MagicMock()
                events = []
                with patch.dict(sys.modules, {'mlx': mlx, 'mlx.core': mlx.core, 'mlx_whisper': whisper}):
                    run_snapshot_speech(inbox, engine, model, 'unused', 'en', lambda *args: events.append(args))
                self.assertEqual([(e[0], e[3]) for e in events], [(0, False), (0, True), (1, True)])

    def test_gpu_readiness_waits_for_offload_log_and_rejects_zero_or_missing_layers(self):
        import io
        from llama_translation import LlamaTranslator
        for line, succeeds in [('offloaded 33/33 layers to GPU\n', True),
                               ('offloaded 0/33 layers to GPU\n', False), ('', False)]:
            with self.subTest(line=line):
                healthy = threading.Event()
                class Logs:
                    def __iter__(self):
                        if not healthy.wait(2): raise AssertionError('Health was never checked')
                        if line: yield line
                    def close(self): pass
                process = MagicMock()
                process.poll.return_value = None
                process.stdout = Logs()
                def health(*args, **kwargs):
                    healthy.set()
                    return io.StringIO('{"status":"ok"}')
                with patch('llama_translation.subprocess.Popen', return_value=process), \
                     patch.object(LlamaTranslator, 'request', side_effect=health), \
                     patch('llama_translation.atexit.register'):
                    config = {'llama_server': 'unused', 'translation_model': 'unused', 'gpu': 0}
                    if succeeds:
                        translator = LlamaTranslator(config, lambda *a, **kw: None, None)
                        self.assertEqual(translator.gpu_layers, 33)
                        translator.close()
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'GPU layer offload'):
                            LlamaTranslator(config, lambda *a, **kw: None, None)
                        process.terminate.assert_called_once()

    def test_speech_engine_detection(self):
        from worker import qwen_language, speech_engine
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            cases = [
                ({"n_mels": 80, "n_audio_ctx": 1500}, "whisper"),
                ({"target": "nemo.collections.asr.models.EncDecRNNTBPEModel"}, "parakeet"),
                ({"model_type": "voxtral_realtime"}, "voxtral_realtime"),
                ({"model_type": "qwen3_asr"}, "qwen3_asr"),
                ({"model_type": "parakeet_tdt"}, "parakeet"),
                ({"model_type": "parakeet_tdt_five_value"}, "phonon2"),
                ({"model_type": "phonon2"}, "phonon2"),
            ]
            for index, (config, expected) in enumerate(cases):
                model = root / str(index)
                model.mkdir()
                (model / "config.json").write_text(json.dumps(config))
                self.assertEqual(speech_engine(model), expected)
            self.assertIsNone(qwen_language("auto"))
            self.assertEqual(qwen_language("fr"), "French")

    def test_stream_batching_preserves_pcm_and_utterance_boundaries(self):
        import numpy as np
        from worker import AudioInbox
        inbox = AudioInbox(0.008, streaming=True)
        frames = [np.full(3200, 0.1 + i / 100, dtype='<f4') for i in range(3)]
        frames += [np.zeros(3200, dtype='<f4') for _ in range(4)]
        frames += [np.full(3200, 0.3, dtype='<f4')]
        for frame in frames:
            inbox.feed(frame.tobytes())
        self.assertFalse(inbox.jobs)  # Streaming doesn't build unused snapshots.
        self.assertEqual(inbox.queued_samples, 25600)
        inbox.closed = True
        jobs = []
        while (job := inbox.next_stream()) is not None:
            jobs.append(job)
        np.testing.assert_array_equal(np.concatenate([j[1] for j in jobs]), np.concatenate(frames))
        self.assertEqual([(u, f) for u, _, f in jobs], [(0, False), (0, False), (0, False), (0, True), (1, False)])
        self.assertEqual(inbox.queued_samples, 0)

    def test_fixture_boundary_tracker_runs_long_without_inference_backlog(self):
        import numpy as np
        from worker import AudioInbox
        tracker = AudioInbox(0.008, tracking_only=True)
        voice = np.full(3200, 0.1, dtype='<f4').tobytes()
        silence = np.zeros(3200, dtype='<f4').tobytes()
        for _ in range(20):
            for _ in range(20): tracker.feed(voice)
            for _ in range(5): tracker.feed(silence)
        self.assertEqual(tracker.utterance, 20)
        self.assertEqual(tracker.queued_samples, 0)
        self.assertFalse(tracker.jobs)
        self.assertLessEqual(tracker.size, 192000)

    def test_stream_backlog_is_bounded_by_duration_not_packets(self):
        import numpy as np
        from worker import AudioInbox
        inbox = AudioInbox(0.008, streaming=True)
        frame = np.full(800, 0.1, dtype='<f4').tobytes()
        for _ in range(500):
            inbox.feed(frame)
        self.assertEqual(inbox.queued_samples, 400000)
        with self.assertRaisesRegex(RuntimeError, "25 seconds"):
            inbox.feed(frame)

    def test_translation_cadence_coalesces_bursts_and_finals_bypass_wait(self):
        from worker import TranslationDispatcher
        called = threading.Event()
        calls = []
        class Translator:
            def translate(self, text, language, source_language, revision, caption_id):
                calls.append((revision, time.monotonic()))
                called.set()
        dispatcher = TranslationDispatcher("unused", ["English"],
                                           translator_factory=lambda _: Translator(), partial_interval=60)
        try:
            dispatcher.submit(0, "first", "fr", 1, False)
            self.assertTrue(called.wait(2))
            called.clear()
            dispatcher.submit(0, "draft", "fr", 2, False)
            dispatcher.submit(0, "correction", "fr", 3, False)
            self.assertFalse(called.wait(0.05))
            dispatcher.submit(0, "final", "fr", 4, True)
            self.assertTrue(called.wait(2))
        finally:
            dispatcher.finish()
        self.assertEqual([r for r, _ in calls], [1, 4])

    def test_speech_priority_defers_translation_and_shutdown_drains_finals(self):
        from worker import TranslationDispatcher
        calls = []
        called = threading.Event()
        class Translator:
            def translate(self, text, language, source_language, revision, caption_id):
                calls.append(revision)
                called.set()
        dispatcher = TranslationDispatcher("unused", ["English"],
                                           translator_factory=lambda _: Translator(),
                                           speech_backlog=lambda: 6400)
        try:
            dispatcher.submit(0, "draft", "fr", 1, False)
            dispatcher.submit(0, "final", "fr", 2, True)
            self.assertFalse(called.wait(0.05))
        finally:
            dispatcher.finish()
        self.assertEqual(calls, [2])

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
