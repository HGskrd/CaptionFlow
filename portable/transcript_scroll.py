"""Synchronize transcript passages without assuming equal translated lengths."""
from dataclasses import dataclass
import weakref


@dataclass(frozen=True)
class Anchor:
    caption_id: str
    progress: float


@dataclass(frozen=True)
class Span:
    caption_id: str
    start: float
    length: float


def anchor_at(offset, spans):
    if not spans: return None
    span = next((span for span in reversed(spans) if span.start <= offset), spans[0])
    return Anchor(span.caption_id, min(1, max(0, (offset-span.start)/max(1, span.length))))


def position_for(anchor, spans, timeline):
    if anchor is None or not spans: return None
    identity = anchor.caption_id if anchor.caption_id in timeline else timeline[0] if timeline else anchor.caption_id
    exact = next((span for span in spans if span.caption_id == identity), None)
    if exact:
        return exact.start + (min(1, max(0, anchor.progress))*exact.length if identity == anchor.caption_id else 0)
    by_id = {span.caption_id: span.start for span in spans}
    if identity in timeline:
        index = timeline.index(identity)
        for candidate in reversed(timeline[:index]):
            if candidate in by_id: return by_id[candidate]
        for candidate in timeline[index+1:]:
            if candidate in by_id: return by_id[candidate]
    return spans[0].start


class TranscriptScrollLink:
    def __init__(self, panes, changed=lambda: None):
        self._panes = [weakref.ref(pane) for pane in panes]
        self.enabled = False
        self.follows_live = True
        self.anchor = None
        self.applying = False
        self.changed = changed
        for pane in panes: pane.scroll_link = self

    @property
    def panes(self): return [pane for ref in self._panes if (pane := ref()) is not None]

    @property
    def timeline(self):
        return [entry['id'] for entry in self.panes[0].document.entries] if self.panes else []

    def configure(self, enabled):
        if enabled == self.enabled: return
        self.enabled = enabled
        self.resume()
        for pane in self.panes: pane.refresh()

    def pause(self, source):
        if not self.enabled or self.applying: return
        self.follows_live = False
        self.anchor = source.scroll_anchor()
        self._apply()

    def resume(self):
        self.follows_live = True; self.anchor = None
        self._apply()

    def _apply(self):
        if self.applying: return
        self.applying = True
        try:
            for pane in self.panes:
                if self.enabled:
                    pane.follows_live = self.follows_live
                    pane.local_anchor = self.anchor
                    pane.restore_position()
                    pane.back.hide()
        finally: self.applying = False
        self.changed()

    def refresh_peers(self, source):
        if not self.enabled or self.applying: return
        self.applying = True
        try:
            for pane in self.panes:
                if pane is not source: pane.restore_position()
        finally: self.applying = False
