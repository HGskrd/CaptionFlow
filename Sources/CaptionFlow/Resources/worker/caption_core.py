"""Pure caption and cache bookkeeping, independently testable without Metal."""
import re
import unicodedata


VOXTRAL_DELAYS = (*range(80, 1201, 80), 2400)


def voxtral_delays(configuration):
    dual = configuration.get("voxtral_dual_delay", False)
    fast = configuration.get("voxtral_delay_ms", 240 if dual else 480)
    slow = configuration.get("voxtral_correction_delay_ms", 1200)
    if type(fast) is not int or fast not in VOXTRAL_DELAYS:
        raise ValueError("Voxtral delay must be a multiple of 80 ms from 80–1200, or 2400 ms.")
    if dual and (type(slow) is not int or slow not in VOXTRAL_DELAYS or slow <= fast):
        raise ValueError("Voxtral correction delay must be a supported delay greater than the draft delay.")
    return (fast, slow) if dual else (fast,)


def merge_transcripts(fast, slow, final=False):
    """Replace the fast prefix with the slower transcript, retaining its live tail.

    Semi-global word alignment permits substitutions, insertions and deletions
    without assuming equal token counts. CJK characters are separate units.
    Without any shared words, wait for overlap (or the authoritative final).
    """
    if final or not fast:
        return slow
    if not slow:
        return fast
    pattern = r"[\u3040-\u30ff\u3400-\u9fff]|[^\W_]+(?:['’][^\W_]+)*"
    a = list(re.finditer(pattern, slow))
    b = list(re.finditer(pattern, fast))
    def key(match):
        return unicodedata.normalize("NFKC", match.group()).casefold()
    left, right = list(map(key, a)), list(map(key, b))
    if not left or not right or not set(left).intersection(right):
        return fast
    costs = list(range(len(right) + 1))
    for i, word in enumerate(left, 1):
        row = [i]
        for j, other in enumerate(right, 1):
            row.append(min(costs[j - 1] + (word != other), costs[j] + 1, row[-1] + 1))
        costs = row
    boundary = min(range(len(costs)), key=lambda j: (
        costs[j], not (j > 0 and right[j - 1] == left[-1]), abs(j - len(left))
    ))
    if boundary >= len(b):
        return slow
    tail = fast[b[boundary].start():]
    # Preserve the fast stream's separator, including unspaced CJK text.
    separator = fast[b[boundary - 1].end():b[boundary].start()] if boundary else " "
    separator = " " if any(c.isspace() for c in separator) else ""
    return slow.rstrip() + separator + tail


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
