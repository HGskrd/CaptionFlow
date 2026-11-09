"""Voxtral sessions sharing weights, live audio encoding and cached conditioning.

The adapter below targets the pinned mlx-audio 0.5.4 streaming internals.
Decoder KV caches remain independent. At close the audio state forks so each
delay keeps its own upstream padding/reflection and finalization semantics.
"""
import copy
import time

from caption_core import merge_transcripts, visible_words


def prepare_voxtral_model(model):
    """Keep legacy checkpoint norms/biases from promoting both transformers.

    Follow the checkpoint's convolution weight dtype; an intentionally float32
    model stays float32. Quantized integer weights are never cast. This only
    changes in-memory execution precision, not the user's model files.
    """
    import mlx.core as mx
    dtype = model.encoder.conv_layers_0_conv.conv.weight.dtype
    model.set_dtype(dtype, predicate=lambda current: current == mx.float32)
    model._ada_scale_delay = -1  # Recompute scales from the prepared weights.
    mx.eval(model.parameters())


class DelayConditioning:
    def __init__(self, model):
        self.model = model
        self.states = {}
        self.dtype = model.encoder.conv_layers_0_conv.conv.weight.dtype

    def activate(self, delay):
        if delay not in self.states:
            self.model._ensure_ada_scales(delay)
            # Older 4-bit checkpoints keep AdaRMSNorm weights in float32.
            # Their scales otherwise promote the entire decoder from fp16 to
            # fp32 at its first layer. Keep conditioning in the audio/decoder
            # activation dtype, without requantizing or changing stored weights.
            scales = [scale.astype(self.dtype) if scale is not None else None
                      for scale in self.model.decoder._ada_scales]
            self.states[delay] = (scales, self.model._ada_scale_delay)
        self.model.decoder._ada_scales, self.model._ada_scale_delay = self.states[delay]


class Decoder:
    def __init__(self, model, delay, conditioning):
        self.model = model
        self.delay = delay
        self.conditioning = conditioning
        conditioning.activate(delay)
        self.session = model.create_streaming_session(transcription_delay_ms=delay, max_tokens=512)
        self.text = ""

    def step(self):
        # mlx-audio keeps AdaRMSNorm conditioning on the shared model, not the
        # session. Restore it before EVERY step; all speech steps run serially.
        # No second weight copy is loaded. Each session owns its encoder/KV state.
        self.conditioning.activate(self.delay)
        deltas = self.session.step(max_decode_tokens=16)
        self.text += "".join(deltas)
        return bool(deltas)

    @property
    def visible(self):
        return visible_words(self.text, final=self.session.done)


class SharedAudio:
    """Encode live PCM once; fork only the short, delay-specific silence tails."""
    def __init__(self, model, decoders):
        self.model = model
        self.sessions = [decoder.session for decoder in decoders]

    def feed(self, samples, final):
        primary = self.sessions[0]
        primary.feed(samples)
        # Ingestion must continue even if the draft decoder has emitted EOS.
        primary._ingest_pending()
        for session in self.sessions[1:]:
            session._adapter_frames = list(primary._adapter_frames)
            session._left_pad_seeded = True
            if final:
                # Copy mutable caches and buffers, never model weights. A
                # single deepcopy preserves aliases between pipeline stages.
                memo = {id(module): module for module in self.model.encoder.modules()}
                memo[id(primary._mel_filters)] = primary._mel_filters
                session._smel, session._sconv, session._senc, session._sproj = copy.deepcopy(
                    (primary._smel, primary._sconv, primary._senc, primary._sproj), memo)
        if final:
            for session in self.sessions:
                session.close()


def run_voxtral_stream(inbox, model, source_language, publish, delays=(480,)):
    decoders = []
    conditioning = DelayConditioning(model)
    shared_audio = None
    last_visible = ""
    started = 0.0
    while (job := inbox.next_stream()) is not None:
        utterance, samples, final = job
        if not decoders:
            decoders = [Decoder(model, delay, conditioning) for delay in delays]
            shared_audio = SharedAudio(model, decoders) if len(decoders) > 1 else None
            last_visible = ""
            started = time.monotonic()
        if shared_audio:
            shared_audio.feed(samples, final)
        else:
            decoders[0].session.feed(samples)
            if final:
                decoders[0].session.close()
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
