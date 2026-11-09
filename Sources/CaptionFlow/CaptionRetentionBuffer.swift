import Foundation

struct CaptionRetentionBuffer {
    private struct Entry {
        var token: CaptionToken
        let appearedAt: Date
    }

    private var entries: [Entry] = []
    var isEmpty: Bool { entries.isEmpty }

    mutating func merge(_ step: CaptionStep, at date: Date = Date()) {
        // Streaming recognizers often add a trailing separator only when the
        // following word arrives. Update retained words without changing IDs.
        for token in step.visibleTokens {
            if let index = entries.firstIndex(where: { $0.token.id == token.id }) {
                entries[index].token.text = token.text
            }
        }
        if step.startsCaption, !entries.isEmpty,
           entries[entries.count - 1].token.text.last?.isWhitespace != true,
           step.appendedToken.text.first?.isWhitespace != true,
           !Self.isUnspacedScript(step.appendedToken.text.first) {
            entries[entries.count - 1].token.text += " "
        }
        if !entries.contains(where: { $0.token.id == step.appendedToken.id }) {
            entries.append(Entry(token: step.appendedToken, appearedAt: date))
        }
        if entries.count > 500 { entries.removeFirst(entries.count - 500) }
    }

    mutating func reconcile(_ revision: CaptionRevision, at date: Date = Date(), preservingWindow: Bool = false) {
        let insertion = entries.firstIndex { revision.previousTokenIDs.contains($0.token.id) } ?? entries.count
        let timestamps = Dictionary(uniqueKeysWithValues: entries.map { ($0.token.id, $0.appearedAt) })
        var tokens = revision.visibleTokens
        if preservingWindow {
            // Don't bring expired words or a paged-out prefix back on screen
            // when a delayed decoder corrects the whole utterance.
            guard entries.contains(where: { revision.previousTokenIDs.contains($0.token.id) }) else { return }
            if revision.firstPreviousTokenID.flatMap({ timestamps[$0] }) == nil {
                if let start = tokens.firstIndex(where: { timestamps[$0.id] != nil }) {
                    tokens = Array(tokens[start...])
                } else { tokens = [] }
            }
        }
        entries.removeAll { revision.previousTokenIDs.contains($0.token.id) }
        let replacement = tokens.enumerated().map { index, token in
            let neighbor = preservingWindow
                ? tokens.prefix(index).reversed().compactMap { timestamps[$0.id] }.first
                    ?? tokens.dropFirst(index + 1).compactMap { timestamps[$0.id] }.first
                : nil
            return Entry(token: token, appearedAt: timestamps[token.id] ?? neighbor ?? date)
        }
        entries.insert(contentsOf: replacement, at: min(insertion, entries.count))
        if revision.startsCaption, insertion > 0, !replacement.isEmpty,
           entries[insertion - 1].token.text.last?.isWhitespace != true,
           replacement[0].token.text.first?.isWhitespace != true,
           !Self.isUnspacedScript(replacement[0].token.text.first) {
            entries[insertion - 1].token.text += " "
        }
        if entries.count > 500 { entries.removeFirst(entries.count - 500) }
    }

    private static func isUnspacedScript(_ character: Character?) -> Bool {
        guard let scalar = character?.unicodeScalars.first?.value else { return false }
        return (0x3040...0x30FF).contains(scalar) || (0x3400...0x9FFF).contains(scalar) || (0xF900...0xFAFF).contains(scalar)
    }

    mutating func tokens(mode: CaptionFlowMode, wordLimit: Int, timeLimit: Double, now: Date = Date()) -> [CaptionToken] {
        switch mode {
        case .sentence:
            break
        case .words:
            if entries.count > wordLimit { entries.removeFirst(entries.count - wordLimit) }
        case .time:
            let cutoff = now.addingTimeInterval(-timeLimit)
            entries.removeAll { $0.appearedAt <= cutoff }
        }
        return entries.map(\.token)
    }

    mutating func text(mode: CaptionFlowMode, wordLimit: Int, timeLimit: Double, now: Date = Date()) -> String {
        tokens(mode: mode, wordLimit: wordLimit, timeLimit: timeLimit, now: now)
            .map(\.text).joined().trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
