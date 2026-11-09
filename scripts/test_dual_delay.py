"""Deterministic dual-delay protocol and alignment checks, without MLX."""
import collections
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Sources/CaptionFlow/Resources/worker"))
from caption_core import merge_transcripts, voxtral_delays
from voxtral_stream import run_voxtral_stream


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
                self.model_test.assertEqual(self.model.delay, self.delay)
                if self.closed:
                    self.done = True
                    return []  # Final must be published even without a new delta.
                if self.chunks == 1:
                    self.chunks += 1
                    return ["The tram leaves tomorrow " if self.delay == 240 else "The train leaves "]
                return []
        class Model:
            delay = None
            sessions = []
            def create_streaming_session(self, transcription_delay_ms, **kwargs):
                self.delay = transcription_delay_ms
                session = Session(self, transcription_delay_ms)
                session.model_test = test
                self.sessions.append(session)
                return session
            def _ensure_ada_scales(self, delay):
                self.delay = delay
        class Inbox:
            jobs = collections.deque([(0, [], False), (0, [], True), (1, [], False), (1, [], True)])
            def next_stream(self):
                return self.jobs.popleft() if self.jobs else None
        test = self
        model, events = Model(), []
        run_voxtral_stream(Inbox(), model, "en", lambda *args, **metadata: events.append((args, metadata)), (240, 1200))
        self.assertEqual(len(model.sessions), 4)
        for utterance in [0, 1]:
            group = [event for event in events if event[0][0] == utterance]
            self.assertEqual(group[0][0][1], "The tram leaves tomorrow")
            self.assertEqual(group[1][0][1], "The train leaves tomorrow")
            self.assertEqual(group[-1][0][1], "The train leaves")
            self.assertTrue(group[-1][0][3])
            self.assertTrue(all(event[1]["revisable"] for event in group))


if __name__ == "__main__":
    unittest.main()
