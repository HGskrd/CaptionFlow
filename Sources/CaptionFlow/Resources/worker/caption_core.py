"""Pure caption and cache bookkeeping, independently testable without Metal."""
import re


def common_prefix(left, right):
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b:
            return index
    return min(len(left), len(right))


def cache_reuse(previous, current, offset):
    # Keep at least one input token to obtain the next-token logits.
    return min(common_prefix(previous, current), max(0, len(current) - 1), offset)


def latest_caption(text, max_words=32, max_chars=220):
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    # Terminal punctuation followed by whitespace, plus CJK sentence boundaries.
    sentences = [p.strip() for p in re.split(r"(?<=[.!?])\s+|(?<=[。！？])", text) if p.strip()]
    sentence = sentences[-1] if sentences else text
    words = sentence.split()
    if len(words) > max_words:
        sentence = "… " + " ".join(words[-max_words:])
    if len(sentence) > max_chars:
        sentence = "…" + sentence[-max_chars:]
    return sentence


def visible_words(text, final=False):
    if final or re.search(r"[\u3000-\u9fff\uac00-\ud7af]", text):
        return text.strip()
    # Don't expose half a word while the tokenizer emits subword fragments.
    match = re.match(r"(?s)(.*[\s.!?。！？,;:])", text)
    return match.group(1).strip() if match else ""
