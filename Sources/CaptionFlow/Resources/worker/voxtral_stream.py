"""Interleaved local Voxtral sessions with shared weights and private KV caches."""
import time

from caption_core import merge_transcripts, visible_words


class Decoder:
    def __init__(self, model, delay):
        self.model = model
        self.delay = delay
        self.session = model.create_streaming_session(transcription_delay_ms=delay, max_tokens=512)
        self.text = ""

    def step(self):
        # mlx-audio keeps AdaRMSNorm conditioning on the shared model, not the
        # session. Restore it before EVERY step; all speech steps run serially.
        # No second weight copy is loaded. Each session owns its encoder/KV state.
        self.model._ensure_ada_scales(self.delay)
        deltas = self.session.step(max_decode_tokens=16)
        self.text += "".join(deltas)
        return bool(deltas)

    @property
    def visible(self):
        return visible_words(self.text, final=self.session.done)


def run_voxtral_stream(inbox, model, source_language, publish, delays=(480,)):
    decoders = []
    last_visible = ""
    started = 0.0
    while (job := inbox.next_stream()) is not None:
        utterance, samples, final = job
        if not decoders:
            decoders = [Decoder(model, delay) for delay in delays]
            last_visible = ""
            started = time.monotonic()
        for decoder in decoders:
            decoder.session.feed(samples)
            if final:
                decoder.session.close()
        # Fast first, then slow, releasing between bounded decode steps. Avoid
        # threads decoding the same mutable MLX speech model concurrently.
        for _ in range(512 if final else 8):
            progress = False
            for index, decoder in enumerate(decoders):
                if decoder.session.done:
                    continue
                progress = decoder.step() or progress
                text = decoders[0].visible
                if len(decoders) == 2:
                    slow = decoders[1]
                    text = merge_transcripts(text, slow.visible, final=final and slow.session.done)
                if text and text != last_visible:
                    last_visible = text
                    publish(utterance, text, source_language, False, time.monotonic() - started,
                            revisable=len(decoders) == 2,
                            corrected=len(decoders) == 2 and index == 1,
                            confirmed_text=decoders[1].visible if len(decoders) == 2 else None)
            if all(d.session.done for d in decoders) or (not final and not progress):
                break
        if final:
            if not all(d.session.done for d in decoders):
                raise RuntimeError("Voxtral could not finalize the current utterance.")
            text = decoders[-1].visible
            if text:
                publish(utterance, text, source_language, True, time.monotonic() - started,
                        revisable=len(decoders) == 2, corrected=len(decoders) == 2,
                        confirmed_text=text if len(decoders) == 2 else None)
            decoders = []
