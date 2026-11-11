"""Memory-only caption presentation. No model work or network access."""
from dataclasses import dataclass
from transcript_scroll import Span, anchor_at, position_for
import json
import os
import re
import time

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QTextCharFormat, QTextLayout, QTextOption
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPlainTextEdit, QPushButton, QApplication, QLabel

MODES = ("Rolling lines", "Phrase blocks", "Transcript")
DEFAULTS = {"original": MODES[0], "translation": MODES[1], "lines": 3, "clear_after": 0, "animation": True}


def load_settings(path):
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULTS)
    if not isinstance(raw, dict): return dict(DEFAULTS)
    return {key: raw.get(key) if raw.get(key) in allowed else DEFAULTS[key] for key, allowed in (
        ("original", MODES), ("translation", MODES), ("lines", (2, 3, 4)),
        ("clear_after", (0, 5, 10)), ("animation", (True, False)))}


@dataclass
class Token:
    id: int
    text: str


class CaptionDocument:
    def __init__(self):
        self.entries = []
        self.phrase = []
        self.next_id = 0
        self.last_change = float("-inf")
        self.cleared = False
        self.hidden_ids = set()
        self.phrase_caption = None
        self.phrase_revision = -1

    def update(self, text, caption_id, revision, final=False, hold_revisions=False, now=None):
        # Words keep their following whitespace. Unspaced text is split by the
        # renderer when necessary, using Qt's grapheme boundaries.
        words = re.findall(r"\S+\s*", text)
        if not words: return
        entry = next((item for item in self.entries if item["id"] == caption_id), None)
        if entry and revision < entry["revision"]: return
        if hold_revisions and entry and revision > entry["revision"] and not final: return
        old = entry["tokens"] if entry else []
        prefix = 0
        while prefix < min(len(old), len(words)) and old[prefix].text.strip() == words[prefix].strip(): prefix += 1
        suffix = 0
        while suffix < min(len(old), len(words))-prefix and old[-1-suffix].text == words[-1-suffix]: suffix += 1
        identities = {index: old[index].id for index in range(prefix)}
        identities.update({len(words)-suffix+offset: old[len(old)-suffix+offset].id for offset in range(suffix)})
        old_middle = old[prefix:len(old)-suffix]; new_middle = words[prefix:len(words)-suffix]
        if old_middle and new_middle:
            if len(old_middle)*len(new_middle) <= 40_000:
                from difflib import SequenceMatcher
                for operation, start, end, new_start, new_end in SequenceMatcher(None, [w.text.strip() for w in old_middle], [w.strip() for w in new_middle], autojunk=False).get_opcodes():
                    if operation in ("equal", "replace"):
                        for offset in range(min(end-start, new_end-new_start)):
                            identities[prefix+new_start+offset] = old_middle[start+offset].id
            else:
                identities.update({prefix+index: old_middle[index].id for index in range(min(len(old_middle), len(new_middle)))})
        tokens = []
        for index, word in enumerate(words):
            if index in identities: identity = identities[index]
            else: identity = self.next_id; self.next_id += 1
            tokens.append(Token(identity, word))
        now = time.monotonic() if now is None else now
        if tokens != old: self.last_change = now; self.cleared = False
        if entry is None:
            entry = {"id": caption_id}; self.entries.append(entry)
        entry.update(revision=revision, tokens=tokens)
        if not hold_revisions or caption_id != self.phrase_caption or revision == self.phrase_revision or final:
            boundaries = [index+1 for index, word in enumerate(words) if word.rstrip()[-1] in ".!?;:。！？؟"]
            count = len(words) if final else boundaries[-1] if boundaries else len(words)//8*8
            if count:
                phrase = tokens[:count]
                if phrase != self.phrase: self.last_change = now; self.cleared = False
                self.phrase = phrase; self.phrase_caption = caption_id; self.phrase_revision = revision
        total = sum(len(item["tokens"]) for item in self.entries)
        while len(self.entries) > 1 and (len(self.entries) > 200 or total > 10_000):
            total -= len(self.entries.pop(0)["tokens"])
        if len(self.entries) == 1: self.entries[0]["tokens"] = self.entries[0]["tokens"][-10_000:]
        self.phrase = self.phrase[-10_000:]

    def tokens(self, mode):
        if mode != "Transcript" and self.cleared: return []
        if mode == "Phrase blocks": return [word for word in self.phrase if word.id not in self.hidden_ids]
        result = []
        for index, entry in enumerate(self.entries):
            words = [Token(word.id, word.text) for word in entry["tokens"]]
            if words and index < len(self.entries)-1: words[-1].text = words[-1].text.rstrip()+"\n"
            result.extend(words)
        return result if mode == "Transcript" else [word for word in result if word.id not in self.hidden_ids]

    def clear_if_idle(self, seconds, last_speech, now=None):
        now = time.monotonic() if now is None else now
        if seconds and now-max(self.last_change, last_speech) >= seconds:
            self.cleared = True
            self.hidden_ids = {word.id for word in self.tokens("Transcript")+self.phrase}


class RollingCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.tokens = []; self.rows = []; self.visible = []; self.positions = {}
        self.lines = 3; self.mode = MODES[0]; self.animate = True
        self.changed = 0.; self.new_ids = set(); self.width_cache = {}; self.layout_cache = {}
        self.last_width = 0
        self.timer = QTimer(self); self.timer.setInterval(16); self.timer.timeout.connect(self.tick)
        font = self.font(); font.setPointSize(28); self.setFont(font)
        self.setAccessibleName("Live captions")

    def motion_enabled(self):
        # Qt maps this to the platform UI animation preference, including Windows
        # accessibility's Animation effects setting. Re-read when rendering.
        if not self.animate: return False
        if os.name == "nt":
            import ctypes
            enabled = ctypes.c_int(1)
            # SPI_GETCLIENTAREAANIMATION is Windows' accessibility Animation
            # effects switch, independent of menu/combo-box animation settings.
            if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
                return bool(enabled.value)
        return QApplication.isEffectEnabled(Qt.UI_AnimateCombo)

    def tick(self):
        self.update()
        if time.monotonic()-self.changed >= .18: self.timer.stop()

    def set_tokens(self, tokens):
        self.tokens = tokens; self.reflow()

    def resizeEvent(self, event):
        super().resizeEvent(event); self.reflow()

    def reflow(self):
        width = max(1, self.width()-50)
        metrics = QFontMetricsF(self.font()); line_height = metrics.height()+5
        starts = {row[0][0] for row in self.rows[1:]} if width == self.last_width else set()
        self.last_width = width
        def measure(text):
            if text not in self.width_cache: self.width_cache[text] = metrics.horizontalAdvance(text)
            return self.width_cache[text]
        rows = []; row = []; used = 0
        def flush():
            nonlocal row, used
            if row: rows.append(row); row = []; used = 0
        def append(text, identity):
            nonlocal used
            if row and (identity in starts or used+measure(text.rstrip()) > width): flush()
            row.append((identity, text)); used += measure(text)
        for token in self.tokens:
            text = token.text.replace("\n", " ")
            if measure(text.rstrip()) > width:
                from PySide6.QtCore import QTextBoundaryFinder
                finder = QTextBoundaryFinder(QTextBoundaryFinder.Grapheme, text)
                # Boundary offsets are UTF-16, unlike Python string indices.
                encoded = text.encode("utf-16-le"); start = 0; part = ""; number = 0
                while (end := finder.toNextBoundary()) != -1:
                    grapheme = encoded[start*2:end*2].decode("utf-16-le"); start = end
                    if part and measure(part+grapheme) > width:
                        append(part, (token.id, number)); number += 1; part = ""
                    part += grapheme
                if part: append(part, (token.id, number))
            else: append(text, (token.id, 0))
            if "\n" in token.text: flush()
        flush()
        capacity = max(1, min(self.lines, int(max(1, self.height()-20)/line_height)))
        visible = rows[max(0, (len(rows)-1)//capacity*capacity):] if self.mode == MODES[1] else rows[-capacity:]
        old_ids = {word[0] for row in self.visible for word in row}
        if visible != self.visible:
            progress = min(1, (time.monotonic()-self.changed)/.16) if self.motion_enabled() else 1
            self.positions = {row[0][0]: self.positions.get(row[0][0], index*line_height)*(1-progress)+index*line_height*progress
                              for index, row in enumerate(self.visible)}
            self.new_ids = {word[0] for row in visible for word in row}-old_ids
            self.changed = time.monotonic()
            if self.motion_enabled(): self.timer.start()
        self.rows = rows; self.visible = visible
        self.setAccessibleDescription("".join(word[1] for row in visible for word in row))
        if len(self.width_cache) > 20_000: self.width_cache.clear()
        if len(self.layout_cache) > 100: self.layout_cache.clear()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        color = self.palette().text().color()
        height = QFontMetricsF(self.font()).height()+5
        capacity = max(1, min(self.lines, int(max(1, self.height()-20)/height)))
        top = max(0, (self.height()-capacity*height)/2)
        age = time.monotonic()-self.changed
        moving = self.motion_enabled() and age < .18
        progress = min(1, age/.16) if moving else 1
        painter.setClipRect(QRectF(25, top, max(1, self.width()-50), capacity*height))
        for index, row in enumerate(self.visible):
            target = index*height
            old = self.positions.get(row[0][0], target)
            y = top + (old+(target-old)*progress if self.mode == MODES[0] else target)
            text = "".join(word[1] for word in row)
            key = tuple(row)
            layout = self.layout_cache.get(key)
            if layout is None:
                layout = QTextLayout(text, self.font())
                option = QTextOption(); option.setWrapMode(QTextOption.NoWrap); layout.setTextOption(option)
                layout.beginLayout(); line = layout.createLine(); line.setLineWidth(max(1, self.width()-50)); layout.endLayout()
                self.layout_cache[key] = layout
            formats = []; offset = 0
            for identity, word in row:
                length = len(word.encode("utf-16-le"))//2
                fmt = QTextLayout.FormatRange(); fmt.start = offset; fmt.length = length
                tint = QColor(color)
                if moving and identity in self.new_ids: tint.setAlphaF(min(1, age/.1))
                fmt.format = QTextCharFormat(); fmt.format.setForeground(tint); formats.append(fmt); offset += length
            layout.draw(painter, QPointF(25, y), formats)


class CaptionPane(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.document = CaptionDocument(); self.mode = MODES[0]; self.follows_live = True
        self.scroll_link = None; self.local_anchor = None; self.spans = []
        self.updating = False; self.layout_pending = False
        self.layout_timer = QTimer(self); self.layout_timer.setSingleShot(True); self.layout_timer.timeout.connect(self.refresh)
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = RollingCanvas(); layout.addWidget(self.canvas)
        self.transcript = QPlainTextEdit(); self.transcript.setReadOnly(True)
        self.transcript.setFont(self.canvas.font()); self.transcript.setFrameShape(QPlainTextEdit.NoFrame)
        self.transcript.viewport().installEventFilter(self)
        self.transcript.installEventFilter(self)
        self.transcript.verticalScrollBar().installEventFilter(self)
        self.transcript.verticalScrollBar().sliderPressed.connect(self.pause_following)
        self.transcript.verticalScrollBar().valueChanged.connect(self.scrolled)
        layout.addWidget(self.transcript)
        self.sync_hint = QLabel("This passage is not available yet."); self.sync_hint.setWordWrap(True)
        self.sync_hint.hide(); layout.addWidget(self.sync_hint)
        self.back = QPushButton("Back to live"); self.back.clicked.connect(self.resume_following); layout.addWidget(self.back, alignment=Qt.AlignRight)
        self.configure(DEFAULTS, "original")

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Resize:
            self.layout_pending = True; self.layout_timer.start(0)
        elif event.type() in (QEvent.Wheel, QEvent.MouseButtonPress, QEvent.KeyPress): self.pause_following()
        return super().eventFilter(obj, event)

    @property
    def linked(self): return self.scroll_link is not None and self.scroll_link.enabled

    def scroll_anchor(self):
        return anchor_at(self.transcript.verticalScrollBar().value(), self.spans)

    def scrolled(self, value):
        if self.updating or self.layout_pending or self.mode != "Transcript": return
        if self.scroll_link and self.scroll_link.applying: return
        self.pause_following()

    def settle_pending_layout(self):
        # Process a deferred resize before interpreting the next user gesture.
        # Otherwise its scrollbar change is mistaken for layout movement.
        if self.layout_pending:
            self.layout_timer.stop()
            self.refresh()

    def pause_following(self):
        if self.mode != "Transcript": return
        self.settle_pending_layout()
        if self.linked: self.scroll_link.pause(self)
        else:
            self.follows_live = False; self.local_anchor = self.scroll_anchor(); self.update_follow_button()
        self.settle_pending_layout()

    def resume_following(self):
        if self.linked: self.scroll_link.resume(); return
        self.follows_live = True; self.local_anchor = None; self.refresh()

    def configure(self, settings, lane):
        self.mode = settings[lane]; self.canvas.mode = self.mode
        self.canvas.lines = settings["lines"]; self.canvas.animate = settings["animation"]
        if not settings["clear_after"]: self.document.cleared = False; self.document.hidden_ids.clear()
        self.canvas.setVisible(self.mode != "Transcript"); self.transcript.setVisible(self.mode == "Transcript")
        self.refresh()

    def reset(self):
        self.document = CaptionDocument(); self.follows_live = True; self.local_anchor = None; self.refresh()

    def receive(self, event, translated=False):
        self.document.update(event["text"], event["caption_id"], event["revision"], event.get("final", False), hold_revisions=translated)
        self.refresh()
        if self.linked: self.scroll_link.refresh_peers(self)

    def update_follow_button(self):
        self.back.setVisible(self.mode == "Transcript" and not self.linked)
        self.back.setText("Live" if self.follows_live else "Back to live")
        self.back.setEnabled(not self.follows_live)

    def refresh(self):
        self.layout_pending = False
        self.update_follow_button()
        if not self.linked: self.sync_hint.hide()
        if self.mode != "Transcript": self.canvas.set_tokens(self.document.tokens(self.mode)); return
        self.updating = True
        try:
            # Each utterance owns a range even when it contains internal newlines.
            segments = [(entry["id"], "".join(word.text for word in entry["tokens"]).strip()) for entry in self.document.entries]
            text = "\n".join(text for _, text in segments)
            if text != self.transcript.toPlainText():
                from PySide6.QtGui import QTextCursor
                old = self.transcript.toPlainText()
                prefix = 0
                for left, right in zip(old, text):
                    if left != right: break
                    prefix += 1
                cursor = QTextCursor(self.transcript.document())
                cursor.setPosition(len(old[:prefix].encode("utf-16-le"))//2)
                cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor); cursor.insertText(text[prefix:])
            self.update_spans(segments)
            self.restore_position()
        finally: self.updating = False

    def update_spans(self, segments):
        document = self.transcript.document(); layout = document.documentLayout()
        rows = {}; row = 0; block = document.begin()
        while block.isValid():
            layout.blockBoundingRect(block)  # Materialize wrapping before counting visual rows.
            rows[block.blockNumber()] = row
            row += max(1, block.layout().lineCount()); block = block.next()
        spans = []; offset = 0
        for identity, text in segments:
            length = len(text.encode("utf-16-le"))//2
            if length:
                first = document.findBlock(offset); last = document.findBlock(offset+length-1)
                if first.isValid() and last.isValid():
                    first_line = first.layout().lineForTextPosition(offset-first.position())
                    last_line = last.layout().lineForTextPosition(offset+length-1-last.position())
                    start = rows[first.blockNumber()] + max(0, first_line.lineNumber())
                    end = rows[last.blockNumber()] + max(0, last_line.lineNumber()) + 1
                    spans.append(Span(identity, start, max(1, end-start)))
            offset += length + 1
        self.spans = spans

    def restore_position(self):
        if self.mode != "Transcript": return
        previous = self.updating; self.updating = True
        try:
            scrollbar = self.transcript.verticalScrollBar()
            follows_live = self.scroll_link.follows_live if self.linked else self.follows_live
            anchor = self.scroll_link.anchor if self.linked else self.local_anchor
            timeline = self.scroll_link.timeline if self.linked else [entry["id"] for entry in self.document.entries]
            position = position_for(anchor, self.spans, timeline)
            missing = self.linked and not follows_live and anchor is not None and anchor.caption_id in timeline and not any(span.caption_id == anchor.caption_id for span in self.spans)
            self.sync_hint.setVisible(missing)
            if follows_live: scrollbar.setValue(scrollbar.maximum())
            elif position is not None: scrollbar.setValue(int(position))
        finally: self.updating = previous
