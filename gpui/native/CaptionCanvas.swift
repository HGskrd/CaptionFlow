import AppKit
import Foundation

/// Reuse the release's document, stable line layout and utterance scroll mapping.
/// GPUI owns rendering and scroll handles; this object owns presentation state.
@MainActor
final class CaptionCanvas {
    var width = 720.0
    var height = 280.0
    private var session: UUID?
    private var layouts: [String: CaptionLineLayout] = [:]
    private var seen: [String: Set<String>] = [:]
    private var spans: [String: [TranscriptSpan]] = [:]
    private var localPositions: [String: TranscriptScrollPosition] = [:]
    private var linkedPosition = TranscriptScrollPosition()
    private var linked = false
    private var timeline: [String] = []
    private var modes: [String: CaptionPresentation] = [:]

    func resize(width: Double, height: Double) {
        self.width = max(1, width); self.height = max(1, height)
    }
    func scroll(lane: String, offset: Double) {
        let position = TranscriptScrollPosition(followsLive: false,
            anchor: TranscriptScrollMapping.anchor(at: max(0, offset), spans: spans[lane] ?? []))
        if linked { linkedPosition = position } else { localPositions[lane] = position }
    }
    func backToLive(lane: String?) {
        if linked || lane == nil {
            linkedPosition = TranscriptScrollPosition(); localPositions = [:]
        } else if let lane { localPositions[lane] = TranscriptScrollPosition() }
    }

    func snapshot(state: AppState) -> [String: Any] {
        let p = state.preferences
        if session != state.originalDocument.sessionID {
            session = state.originalDocument.sessionID
            layouts = [:]; seen = [:]; spans = [:]; modes = [:]
            backToLive(lane: nil)
        }
        let newLinked = p.displayMode == .both && !p.targets.isEmpty &&
            p.effectiveCaptionPresentation == .transcript && p.effectiveTranslationPresentation == .transcript
        if linked != newLinked { backToLive(lane: nil) }
        linked = newLinked
        timeline = state.originalDocument.utterances.map(\.id)
        let font = p.typeface == "System" ? NSFont.systemFont(ofSize: p.fontSize, weight: .medium)
            : NSFont(name: p.typeface, size: p.fontSize) ?? NSFont.systemFont(ofSize: p.fontSize)
        let lineHeight = ceil(font.ascender - font.descender + font.leading) + 5
        let capacity = max(1, min(p.effectiveCaptionVisibleLines, Int(height / lineHeight)))
        let animate = p.effectiveCaptionGentleAnimation && !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        var inputs: [(String, CaptionPresentation, LiveCaptionDocument, [CaptionToken])] = []
        if p.displayMode != .translated { inputs.append(("original", p.effectiveCaptionPresentation, state.originalDocument, state.originalTokens)) }
        if p.displayMode != .original {
            for language in p.targets { inputs.append(("translation:" + language, p.effectiveTranslationPresentation,
                state.translationDocuments[language] ?? LiveCaptionDocument(), state.translationTokens[language] ?? [])) }
        }
        var lanes: [[String: Any]] = []
        for (key, mode, document, classic) in inputs {
            if modes[key] != mode { localPositions[key] = TranscriptScrollPosition(); layouts[key] = nil; seen[key] = nil }
            modes[key] = mode
            if mode == .classic {
                lanes.append(["id": key, "mode": mode.rawValue, "classicText": classic.map(\.text).joined(),
                              "rows": [], "capacity": min(3, capacity), "followsLive": true])
                continue
            }
            var layout = layouts[key, default: CaptionLineLayout()]
            layout.update(tokens: document.visibleTokens(for: mode), width: width,
                          fontKey: "\(p.typeface):\(p.fontSize)") {
                ($0 as NSString).size(withAttributes: [.font: font]).width
            }
            layouts[key] = layout
            let currentIDs = Set(layout.lines.flatMap(\.words).map(\.id))
            let entering = currentIDs.subtracting(seen[key] ?? [])
            seen[key] = currentIDs
            var rows = layout.lines
            if mode == .rolling { rows = Array(rows.suffix(capacity)) }
            if mode == .phrase {
                rows = Array(rows.dropFirst(max(0, (rows.count - 1) / capacity * capacity)))
            }
            let byToken = Dictionary(uniqueKeysWithValues: document.utterances.flatMap { utterance in
                utterance.tokens.map { ($0.id, utterance.id) }
            })
            var transcriptSpans: [TranscriptSpan] = []
            let outputRows: [[String: Any]] = rows.enumerated().map { index, row in
                let tokenID = row.words.first?.id.split(separator: ":").first.flatMap { Int($0) }
                let captionID = tokenID.flatMap { byToken[$0] } ?? ""
                if mode == .transcript {
                    if transcriptSpans.last?.captionID == captionID {
                        let previous = transcriptSpans.removeLast()
                        transcriptSpans.append(TranscriptSpan(captionID: captionID, start: previous.start, length: previous.length + lineHeight))
                    }
                    else { transcriptSpans.append(TranscriptSpan(captionID: captionID, start: Double(index) * lineHeight, length: lineHeight)) }
                }
                return ["id": row.id, "captionID": captionID, "speaker_id": state.speakers[captionID] ?? "", "text": row.words.map(\.text).joined(),
                        "words": row.words.map { ["id": $0.id, "text": $0.text, "width": $0.width, "entering": entering.contains($0.id)] as [String: Any] }]
            }
            spans[key] = transcriptSpans
            let position = linked ? linkedPosition : localPositions[key] ?? TranscriptScrollPosition()
            let offset = position.anchor.flatMap { TranscriptScrollMapping.position(for: $0, spans: transcriptSpans, timeline: timeline) } ?? 0
            let pending = position.anchor.map { anchor in linked && timeline.contains(anchor.captionID) && !transcriptSpans.contains(where: { $0.captionID == anchor.captionID }) } ?? false
            lanes.append(["id": key, "mode": mode.rawValue, "rows": outputRows, "capacity": capacity,
                          "followsLive": position.followsLive, "offset": offset, "pending": pending])
        }
        return ["session": session?.uuidString ?? "", "lanes": lanes, "linked": linked,
                "lineHeight": lineHeight, "gentleAnimation": animate,
                "fontFamily": font.familyName ?? ".SystemUIFont", "systemFont": p.typeface == "System"]
    }
}
