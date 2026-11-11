import AppKit
import SwiftUI

/// Native selection and scrolling; SwiftUI owns the shared semantic position.
struct CaptionTranscript: NSViewRepresentable {
    var segments: [TranscriptSegment]
    var timeline: [String]
    var font: NSFont
    var color: NSColor
    @Binding var position: TranscriptScrollPosition

    final class Scroller: NSScroller {
        var interaction: ((Bool) -> Void)?
        override func mouseDown(with event: NSEvent) {
            interaction?(true); defer { interaction?(false) }
            super.mouseDown(with: event)
        }
    }
    final class TextView: NSTextView {
        var interaction: ((Bool) -> Void)?
        override func keyDown(with event: NSEvent) {
            interaction?(true); defer { interaction?(false) }
            super.keyDown(with: event)
        }
        override func mouseDown(with event: NSEvent) {
            interaction?(true); defer { interaction?(false) }
            super.mouseDown(with: event)
        }
    }
    final class ScrollView: NSScrollView {
        var interaction: ((Bool) -> Void)?
        var resized: (() -> Void)?
        override func scrollWheel(with event: NSEvent) {
            guard event.scrollingDeltaY != 0 else { super.scrollWheel(with: event); return }
            interaction?(true); defer { interaction?(false) }
            super.scrollWheel(with: event)
        }
        override func setFrameSize(_ newSize: NSSize) {
            let changed = frame.size != newSize
            super.setFrameSize(newSize)
            if changed { resized?() }
        }
    }

    final class Coordinator {
        private var parent: CaptionTranscript?
        private weak var scroll: ScrollView?
        private var observer: NSObjectProtocol?
        private var font: NSFont?
        private var color: NSColor?
        private var layoutWidth: CGFloat = -1
        private var layoutSegments: [TranscriptSegment] = []
        private var rendering = false
        private var interacting = false
        private(set) var spans: [TranscriptSpan] = []

        deinit { if let observer { NotificationCenter.default.removeObserver(observer) } }

        func attach(_ scroll: ScrollView) {
            self.scroll = scroll
            let interaction: (Bool) -> Void = { [weak self] began in
                guard let self else { return }
                if began { self.interacting = true }
                self.publishPosition()
                if !began { self.interacting = false }
            }
            scroll.interaction = interaction
            (scroll.verticalScroller as? Scroller)?.interaction = interaction
            (scroll.documentView as? TextView)?.interaction = interaction
            scroll.contentView.postsBoundsChangedNotifications = true
            observer = NotificationCenter.default.addObserver(forName: NSView.boundsDidChangeNotification, object: scroll.contentView, queue: .main) { [weak self] _ in
                self?.publishPosition()
            }
            scroll.resized = { [weak self] in
                guard let self, let parent = self.parent, !self.rendering else { return }
                self.update(parent)
            }
        }

        private func publishPosition() {
            guard !rendering, interacting, let scroll, let parent else { return }
            let next = TranscriptScrollPosition(followsLive: false,
                anchor: TranscriptScrollMapping.anchor(at: scroll.contentView.bounds.minY, spans: spans))
            if parent.position != next { parent.position = next }
        }

        func update(_ parent: CaptionTranscript) {
            self.parent = parent
            guard let scroll, let view = scroll.documentView as? NSTextView,
                  let manager = view.layoutManager, let container = view.textContainer else { return }
            rendering = true; defer { rendering = false }
            let origin = scroll.contentView.bounds.origin
            let text = parent.segments.map(\.text).joined(separator: "\n")
            let attributes: [NSAttributedString.Key: Any] = [.font: parent.font, .foregroundColor: parent.color]
            if font != parent.font || color != parent.color {
                view.font = parent.font; view.textColor = parent.color; view.typingAttributes = attributes
                view.textStorage?.setAttributes(attributes, range: NSRange(location: 0, length: view.string.utf16.count))
                font = parent.font; color = parent.color; layoutWidth = -1
            }
            if view.string != text {
                let prefix = view.string.commonPrefix(with: text, options: .literal)
                let offset = prefix.utf16.count
                view.textStorage?.replaceCharacters(in: NSRange(location: offset, length: view.string.utf16.count - offset),
                    with: NSAttributedString(string: String(text.dropFirst(prefix.count)), attributes: attributes))
            }
            if layoutWidth != scroll.contentView.bounds.width || layoutSegments != parent.segments || font != view.font {
                manager.ensureLayout(for: container)
                var offset = 0
                spans = parent.segments.compactMap { segment in
                    let length = segment.text.utf16.count
                    defer { offset += length + 1 }
                    guard length > 0 else { return nil }
                    let glyphs = manager.glyphRange(forCharacterRange: NSRange(location: offset, length: length), actualCharacterRange: nil)
                    guard glyphs.length > 0 else { return nil }
                    let first = manager.lineFragmentRect(forGlyphAt: glyphs.location, effectiveRange: nil)
                    let last = manager.lineFragmentRect(forGlyphAt: NSMaxRange(glyphs) - 1, effectiveRange: nil)
                    return TranscriptSpan(captionID: segment.id, start: first.minY + view.textContainerInset.height,
                                          length: max(1, last.maxY - first.minY))
                }
                layoutWidth = scroll.contentView.bounds.width; layoutSegments = parent.segments
            }
            if parent.position.followsLive { view.scrollToEndOfDocument(nil) }
            else {
                let target = parent.position.anchor.flatMap {
                    TranscriptScrollMapping.position(for: $0, spans: spans, timeline: parent.timeline)
                } ?? origin.y
                let maximum = max(0, view.frame.height - scroll.contentView.bounds.height)
                scroll.contentView.scroll(to: NSPoint(x: origin.x, y: min(maximum, max(0, target))))
                scroll.reflectScrolledClipView(scroll.contentView)
            }
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator() }
    static func makeScrollView() -> ScrollView {
        let scroll = ScrollView()
        scroll.hasVerticalScroller = true; scroll.drawsBackground = false
        scroll.verticalScroller = Scroller()
        let view = TextView(frame: .zero)
        view.isEditable = false; view.isSelectable = true; view.isRichText = false; view.drawsBackground = false
        view.isVerticallyResizable = true; view.isHorizontallyResizable = false
        view.autoresizingMask = [.width]
        view.textContainer?.widthTracksTextView = true
        view.textContainer?.containerSize = NSSize(width: 0, height: CGFloat.greatestFiniteMagnitude)
        view.textContainerInset = NSSize(width: 0, height: 8)
        scroll.documentView = view
        return scroll
    }
    func makeNSView(context: Context) -> ScrollView {
        let scroll = Self.makeScrollView(); context.coordinator.attach(scroll); return scroll
    }
    func updateNSView(_ scroll: ScrollView, context: Context) { context.coordinator.update(self) }
}
