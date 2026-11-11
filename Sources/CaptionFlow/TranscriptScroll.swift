import Foundation

struct TranscriptSegment: Identifiable, Equatable {
    let id: String
    let text: String
}

struct TranscriptAnchor: Equatable {
    let captionID: String
    let progress: Double
}

struct TranscriptScrollPosition: Equatable {
    var followsLive = true
    var anchor: TranscriptAnchor?
}

struct TranscriptSpan {
    let captionID: String
    let start: Double
    let length: Double
}

/// Link by utterance identity, then approximate progress inside that utterance.
/// Whole-document percentages drift when translation length or availability differs.
enum TranscriptScrollMapping {
    static func anchor(at offset: Double, spans: [TranscriptSpan]) -> TranscriptAnchor? {
        guard let span = spans.last(where: { $0.start <= offset }) ?? spans.first else { return nil }
        return TranscriptAnchor(captionID: span.captionID,
                                progress: min(1, max(0, (offset - span.start) / max(1, span.length))))
    }

    static func position(for anchor: TranscriptAnchor, spans: [TranscriptSpan], timeline: [String]) -> Double? {
        guard !spans.isEmpty else { return nil }
        // A paused passage may age out of bounded history. Both panes then use
        // the oldest retained source passage, rather than unrelated percentages.
        let identity = timeline.contains(anchor.captionID) ? anchor.captionID : timeline.first ?? anchor.captionID
        if let exact = spans.first(where: { $0.captionID == identity }) {
            return exact.start + (identity == anchor.captionID ? min(1, max(0, anchor.progress)) * exact.length : 0)
        }
        let byID = Dictionary(uniqueKeysWithValues: spans.map { ($0.captionID, $0.start) })
        if let index = timeline.firstIndex(of: identity) {
            for id in timeline.prefix(index).reversed() {
                if let position = byID[id] { return position }
            }
            for id in timeline.dropFirst(index + 1) {
                if let position = byID[id] { return position }
            }
        }
        return spans.first?.start
    }
}

extension LiveCaptionDocument {
    var transcriptSegments: [TranscriptSegment] {
        utterances.map { TranscriptSegment(id: $0.id, text: $0.tokens.map(\.text).joined().trimmingCharacters(in: .whitespacesAndNewlines)) }
    }
}
