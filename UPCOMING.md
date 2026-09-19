# Upcoming experiments

These are research directions, not implemented behavior. They should remain behind explicit experimental settings until their latency, memory use, translation quality, and visual stability have been measured on Apple Silicon.

## Logit-aware translation revision

The current translator greedily regenerates from the longest reusable prompt prefix. Local inference gives CaptionFlow access to the full output distribution, so revisions can use more than the previously selected tokens.

Proposed approach:

1. Retain top-k log probabilities, entropy, top-one/top-two margin, cumulative sequence score, and periodic KV-cache snapshots for the recent target suffix.
2. When the ASR hypothesis changes, teacher-force the displayed translation under the newest source context and identify the earliest recent token whose probability materially deteriorates.
3. Batch several candidates: the existing suffix, a fresh decode, alternatives from uncertain tokens, and one or more deeper rollback points.
4. Rescore every candidate under the same current source prompt using length-normalized conditional log probability. Raw scores produced under different prompt lengths are not directly comparable.
5. Optionally use posterior consensus or minimum-Bayes-risk selection rather than trusting a single highest-scoring sequence.
6. Allow the selected suffix to rewrite visible translation. Measure the result with translation quality, latency, revision rate, and normalized erasure rather than treating stability as an absolute constraint.

The first prototype should retain roughly 16–32 recent target tokens, use uncertainty-derived rollback points, and refresh on meaningful ASR changes or a short cadence rather than on every subword. A user-facing responsiveness control could later map to revision depth, candidate count, and refresh cadence.

Open questions:

- How well calibrated are Qwen's token probabilities for translation quality across languages?
- Is likelihood plus continuity sufficient, or does consensus produce fewer oscillations?
- How much KV state can be retained before memory pressure offsets the benefit of the smaller translation model?
- Should uncertain text be visually distinguished, or should CaptionFlow simply replace it without animation?

## Shared-audio transcription and translation

`Voxtral-Mini-4B-Realtime-2602` is a purpose-built streaming ASR model. Its supported task is transcription; native translation should not be assumed from the released realtime weights. The earlier general-purpose Voxtral Mini 3B and Voxtral Small 24B models support prompted speech translation, but are not the same low-latency streaming model.

A plausible experiment with a general-purpose audio-language model is:

1. Encode an expanding audio segment once.
2. Reuse or broadcast the audio representation into independent decoder branches.
3. Batch one prompt for the original-language transcript and one prompt per target language.
4. Maintain separate generated-token caches and logits for every branch while sharing model weights and audio features.
5. Apply the logit-aware revision scheme independently to the transcript and translations, and test whether cross-branch agreement is useful as a confidence signal.

Separate decoder branches are preferable to one structured response containing both outputs: they can generate concurrently, fail independently, and extend naturally to multiple target languages. A custom implementation must avoid recomputing the audio encoder for every branch; merely duplicating the audio input in a batch would save little beyond shared weights.

Candidate comparisons:

- Current baseline: Voxtral Realtime transcription followed by Qwen text translation.
- General Voxtral: shared audio encoding with batched transcript and direct-translation prompts.
- Future fine-tune: realtime Voxtral with explicit transcription and translation task branches.

Evaluate time to first stable word, end-to-end lag, memory, source transcription accuracy, translation quality, and visible revision behavior. The direct-audio path may improve translation by retaining acoustic evidence, but it may also be slower and harder to stabilize than the existing cascade.

## References

- [Qwen 3.5 0.8B](https://huggingface.co/Qwen/Qwen3.5-0.8B)
- [Qwen 3.5 0.8B MLX 4-bit](https://huggingface.co/mlx-community/Qwen3.5-0.8B-MLX-4bit)
- [Voxtral Realtime](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602)
- [General-purpose Voxtral](https://mistral.ai/news/voxtral/)
- [Re-translation versus Streaming for Simultaneous Translation](https://aclanthology.org/2020.iwslt-1.27/)
- [Dynamic Masking for Improved Stability in Spoken Language Translation](https://arxiv.org/abs/2006.00249)
