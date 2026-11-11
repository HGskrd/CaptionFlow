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
    var segments: [TranscriptSegment] = []
    var timeline: [String] = []
    var linkedScroll: Binding<TranscriptScrollPosition>? = nil
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var layout = CaptionLineLayout()
    @State private var localScroll = TranscriptScrollPosition()
    private var scrollPosition: Binding<TranscriptScrollPosition> { linkedScroll ?? $localScroll }
    @State private var seenWordIDs = Set<String>()
    @State private var enteringWordIDs = Set<String>()
    private var font: NSFont {
        typeface == "System" ? .systemFont(ofSize: fontSize, weight: .medium) : NSFont(name: typeface, size: fontSize) ?? .systemFont(ofSize: fontSize)
    }
    var body: some View {
        if mode == .transcript {
            VStack(alignment: .trailing, spacing: 4) {
                CaptionTranscript(segments: segments, timeline: timeline, font: font, color: NSColor(foreground), position: scrollPosition)
                    .accessibilityLabel("\(label) transcript")
                if let position = linkedScroll?.wrappedValue, !position.followsLive,
                   let anchor = position.anchor, timeline.contains(anchor.captionID),
                   !segments.contains(where: { $0.id == anchor.captionID }) {
                    Text("This passage is not available yet.").font(.caption).foregroundStyle(foreground.opacity(0.7))
                }
                if linkedScroll == nil {
                    Button(localScroll.followsLive ? "Live" : "Back to live", systemImage: "arrow.down.to.line") { localScroll = TranscriptScrollPosition() }
                        .disabled(localScroll.followsLive).controlSize(.small)
                }
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
