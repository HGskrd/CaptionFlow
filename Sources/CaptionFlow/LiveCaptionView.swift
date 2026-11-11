import AppKit
import SwiftUI

struct LiveCaptionView: View {
    let tokens: [CaptionToken]
    let label: String
    let mode: CaptionPresentation
    let typeface: String
    let fontSize: Double
    let foreground: Color
    let visibleLines: Int
    let gentleAnimation: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var layout = CaptionLineLayout()
    @State private var followsLive = true
    @State private var seenWordIDs = Set<String>()
    @State private var enteringWordIDs = Set<String>()
    private var font: NSFont {
        typeface == "System" ? .systemFont(ofSize: fontSize, weight: .medium) : NSFont(name: typeface, size: fontSize) ?? .systemFont(ofSize: fontSize)
    }
    var body: some View {
        if mode == .transcript {
            VStack(alignment: .trailing, spacing: 4) {
                CaptionTranscript(text: tokens.map(\.text).joined(), font: font, color: NSColor(foreground), followsLive: $followsLive)
                    .accessibilityLabel("\(label) transcript")
                Button(followsLive ? "Live" : "Back to live", systemImage: "arrow.down.to.line") { followsLive = true }
                    .disabled(followsLive).controlSize(.small)
            }
        } else {
            GeometryReader { geometry in
                let lineHeight = ceil(font.ascender - font.descender + font.leading) + 5
                let capacity = max(1, min(visibleLines, Int(geometry.size.height / lineHeight)))
                let rows = visibleRows(capacity: capacity)
                ZStack(alignment: .topLeading) {
                    ForEach(Array(rows.enumerated()), id: \.element.id) { index, line in
                        HStack(alignment: .firstTextBaseline, spacing: 0) {
                            ForEach(line.words) { word in
                                CaptionWord(word: word, font: font, animate: gentleAnimation && !reduceMotion && enteringWordIDs.contains(word.id))
                            }
                        }
                        .frame(height: lineHeight, alignment: .topLeading)
                        .offset(y: CGFloat(index) * lineHeight)
                        .animation(gentleAnimation && !reduceMotion ? .easeOut(duration: 0.16) : nil, value: index)
                    }
                }
                .frame(width: geometry.size.width, height: CGFloat(capacity) * lineHeight, alignment: .topLeading)
                .clipped()
                .frame(height: geometry.size.height, alignment: .center)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("\(label): \(rows.flatMap(\.words).map(\.text).joined())")
                .onAppear { update(width: geometry.size.width) }
                .onChange(of: tokens) { _, _ in update(width: geometry.size.width) }
                .onChange(of: geometry.size.width) { _, width in update(width: width) }
                .onChange(of: typeface) { _, _ in update(width: geometry.size.width) }
                .onChange(of: fontSize) { _, _ in update(width: geometry.size.width) }
            }
        }
    }
    private func update(width: CGFloat) {
        layout.update(tokens: tokens, width: width, fontKey: "\(typeface):\(fontSize)") {
            ($0 as NSString).size(withAttributes: [.font: font]).width
        }
        let current = Set(layout.lines.flatMap(\.words).map(\.id))
        enteringWordIDs = current.subtracting(seenWordIDs)
        seenWordIDs = current
    }
    private func visibleRows(capacity: Int) -> [CaptionLineLayout.Line] {
        if mode == .phrase {
            // Complete blocks replace one another; only rolling mode slides lines.
            let start = max(0, (layout.lines.count - 1) / capacity * capacity)
            return Array(layout.lines.dropFirst(start))
        }
        return Array(layout.lines.suffix(capacity))
    }
}

private struct CaptionWord: View {
    let word: CaptionLineLayout.Word
    let font: NSFont
    let animate: Bool
    @State private var appeared = false
    var body: some View {
        Text(word.text).font(Font(font)).fixedSize()
            .frame(width: word.width, alignment: .leading)
            .opacity(appeared || !animate ? 1 : 0)
            .onAppear {
                withAnimation(animate ? .easeOut(duration: 0.10) : nil) { appeared = true }
            }
    }
}

/// Native text selection and scroll-wheel tracking on macOS 14. SwiftUI owns
/// follow state; the scroll view only reports user interaction and renders text.
private struct CaptionTranscript: NSViewRepresentable {
    var text: String
    var font: NSFont
    var color: NSColor
    @Binding var followsLive: Bool

    final class Scroller: NSScroller {
        var userScrolled: (() -> Void)?
        override func mouseDown(with event: NSEvent) { userScrolled?(); super.mouseDown(with: event) }
    }
    final class ScrollView: NSScrollView {
        var userScrolled: (() -> Void)?
        override func scrollWheel(with event: NSEvent) {
            if event.scrollingDeltaY != 0 { userScrolled?() }
            super.scrollWheel(with: event)
        }
    }
    final class Coordinator {
        var font: NSFont?
        var color: NSColor?
    }
    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeNSView(context: Context) -> ScrollView {
        let scroll = ScrollView()
        scroll.hasVerticalScroller = true; scroll.drawsBackground = false
        scroll.verticalScroller = Scroller()
        let view = NSTextView(frame: .zero)
        view.isEditable = false; view.isSelectable = true; view.isRichText = false; view.drawsBackground = false
        view.isVerticallyResizable = true; view.isHorizontallyResizable = false
        view.autoresizingMask = [.width]
        view.textContainer?.widthTracksTextView = true
        view.textContainer?.containerSize = NSSize(width: 0, height: CGFloat.greatestFiniteMagnitude)
        view.textContainerInset = NSSize(width: 0, height: 8)
        scroll.documentView = view
        return scroll
    }
    func updateNSView(_ scroll: ScrollView, context: Context) {
        scroll.userScrolled = { if followsLive { followsLive = false } }
        (scroll.verticalScroller as? Scroller)?.userScrolled = scroll.userScrolled
        guard let view = scroll.documentView as? NSTextView else { return }
        let origin = scroll.contentView.bounds.origin
        let attributes: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: color]
        if context.coordinator.font != font || context.coordinator.color != color {
            view.font = font; view.textColor = color; view.typingAttributes = attributes
            view.textStorage?.setAttributes(attributes, range: NSRange(location: 0, length: view.string.utf16.count))
            context.coordinator.font = font; context.coordinator.color = color
        }
        if view.string != text {
            let prefix = view.string.commonPrefix(with: text, options: .literal)
            let offset = prefix.utf16.count
            view.textStorage?.replaceCharacters(in: NSRange(location: offset, length: view.string.utf16.count - offset),
                                                with: NSAttributedString(string: String(text.dropFirst(prefix.count)), attributes: attributes))
        }
        view.layoutManager?.ensureLayout(for: view.textContainer!)
        if followsLive { view.scrollToEndOfDocument(nil) }
        else { scroll.contentView.scroll(to: origin); scroll.reflectScrolledClipView(scroll.contentView) }
    }
}
