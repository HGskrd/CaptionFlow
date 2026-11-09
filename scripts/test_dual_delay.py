"""Deterministic dual-delay protocol and alignment checks, without MLX."""
import collections
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Sources/CaptionFlow/Resources/worker"))
from caption_core import merge_transcripts, voxtral_delays
from voxtral_stream import run_voxtral_stream, SharedAudio


class DualDelayTest(unittest.TestCase):
    def test_delay_validation(self):
        self.assertEqual(voxtral_delays({}), (480,))
        self.assertEqual(voxtral_delays({"voxtral_dual_delay": True}), (240, 1200))
        for fast, slow in [(0, 1200), (240, 1000), (480, 480), (1200, 960), (True, 2400)]:
            with self.assertRaises(ValueError):
                voxtral_delays({"voxtral_dual_delay": True, "voxtral_delay_ms": fast,
                                "voxtral_correction_delay_ms": slow})

    def test_corrections_keep_the_fast_tail(self):
        cases = [
            ("The train leaves at nine tomorrow morning.", "The train leaves at ten", "The train leaves at ten tomorrow morning."),
            ("I like trains today", "I really like trains", "I really like trains today"),
            ("I really like trains today", "I like trains", "I like trains today"),
            ("We had a meeting today", "We had an important meeting", "We had an important meeting today"),
            ("Go go home now", "Go home", "Go home now"),
            ("你好世界今天", "您好世界", "您好世界今天"),
            ("C'est très bien demain", "C’est très bien", "C’est très bien demain"),
        ]
        for fast, slow, expected in cases:
            with self.subTest(fast=fast, slow=slow):
                self.assertEqual(merge_transcripts(fast, slow), expected)
        self.assertEqual(merge_transcripts("different words", "new phrase"), "different words")
        self.assertEqual(merge_transcripts("different words", "new phrase", final=True), "new phrase")
        self.assertEqual(merge_transcripts("The train", "The train leaves"), "The train leaves")

    def test_shared_conditioning_finalization_and_utterance_reset(self):
        class Scale(int):
            def astype(self, dtype):
                test.assertEqual(dtype, "float16")
                return self
        class Session:
            def __init__(self, model, delay):
                self.model, self.delay = model, delay
                self.done = self.closed = False
                self.chunks = 0
            def feed(self, samples):
                self.chunks += 1
            def close(self):
                self.closed = True
            def step(self, **kwargs):
                self.model_test.assertEqual(self.model.decoder._ada_scales, [self.delay])
                if self.closed:
                    self.done = True
                    return []  # Final must be published even without a new delta.
                if self.chunks == 1:
                    self.chunks += 1
                    return ["The tram leaves tomorrow " if self.delay == 240 else "The train leaves "]
                return []
        class Model:
            encoder = SimpleNamespace(conv_layers_0_conv=SimpleNamespace(
                conv=SimpleNamespace(weight=SimpleNamespace(dtype="float16"))))
            decoder = SimpleNamespace(_ada_scales=None)
            _ada_scale_delay = None
            recomputations = 0
            sessions = []
            def create_streaming_session(self, transcription_delay_ms, **kwargs):
                self._ensure_ada_scales(transcription_delay_ms)
                session = Session(self, transcription_delay_ms)
                session.model_test = test
                self.sessions.append(session)
                return session
            def _ensure_ada_scales(self, delay):
                if self._ada_scale_delay != delay:
                    self.recomputations += 1
                    self.decoder._ada_scales = [Scale(delay)]
                    self._ada_scale_delay = delay
        class Audio:
            def __init__(self, model, decoders):
                self.sessions = [d.session for d in decoders]
            def feed(self, samples, final):
                for session in self.sessions:
                    session.feed(samples)
                    if final:
                        session.close()
        class Inbox:
            jobs = collections.deque([(0, [], False), (0, [], True), (1, [], False), (1, [], True)])
            def next_stream(self):
                return self.jobs.popleft() if self.jobs else None
        test = self
        model, events = Model(), []
        with patch("voxtral_stream.SharedAudio", Audio):
            run_voxtral_stream(Inbox(), model, "en", lambda *args, **metadata: events.append((args, metadata)), (240, 1200))
        self.assertEqual(model.recomputations, 2)
        self.assertEqual(len(model.sessions), 4)
        for utterance in [0, 1]:
            group = [event for event in events if event[0][0] == utterance]
            self.assertEqual(group[0][0][1], "The tram leaves tomorrow")
            self.assertEqual(group[1][0][1], "The train leaves tomorrow")
            self.assertEqual(group[-1][0][1], "The train leaves")
            self.assertTrue(group[-1][0][3])
            self.assertTrue(all(event[1]["revisable"] for event in group))

    def test_shared_audio_encodes_once_and_forks_only_mutable_tail_state(self):
        class Encoder:
            def modules(self):
                return [self]
        encoder = Encoder()
        class Session:
            def __init__(self):
                self._adapter_frames = []
                self._left_pad_seeded = False
                self._mel_filters = object()
                self._smel = {"history": [], "weights": encoder}
                self._sconv = self._senc = self._sproj = self._smel
                self.closed = False
                self.ingests = 0
            def feed(self, samples):
                self.samples = samples
            def _ingest_pending(self):
                self.ingests += 1
                self._smel["history"].extend(self.samples)
                self._adapter_frames.append(tuple(self.samples))
            def close(self):
                self.closed = True
        first, second = Session(), Session()
        shared = SharedAudio(SimpleNamespace(encoder=encoder),
                             [SimpleNamespace(session=s) for s in (first, second)])
        shared.feed([1, 2], False)
        self.assertEqual((first.ingests, second.ingests), (1, 0))
        self.assertEqual(second._adapter_frames, [(1, 2)])
        self.assertFalse(second.closed)
        shared.feed([3], True)
        self.assertEqual(second._smel["history"], [1, 2, 3])
        self.assertIs(second._smel["weights"], encoder)
        self.assertIs(second._smel, second._senc)
        self.assertIsNot(second._smel, first._smel)
        second._smel["history"].append(4)
        self.assertEqual(first._smel["history"], [1, 2, 3])
        self.assertTrue(first.closed and second.closed)


if __name__ == "__main__":
    unittest.main()
