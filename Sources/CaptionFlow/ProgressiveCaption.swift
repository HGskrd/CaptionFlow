import Foundation
import NaturalLanguage

struct CaptionToken: Identifiable, Equatable {
    let id: Int
    var text: String
}

struct CaptionStep: Equatable {
    let fullText: String
    let appendedToken: CaptionToken
    let visibleTokens: [CaptionToken]
    let startsCaption: Bool
}

struct CaptionRevision: Equatable {
    let previousTokenIDs: Set<Int>
    let visibleTokens: [CaptionToken]
    let startsCaption: Bool
}

/// Turns irregular model batches into a steady subtitle cadence. The target
/// follows recent delivery timing, while damping and hard bounds prevent a
/// slow decode from becoming a burst followed by a long blank pause.
struct CaptionPacer {
    private(set) var milliseconds = 180.0
    private var lastDelivery: Date?

    mutating func observe(pendingWords: Int, at now: Date = Date()) {
        defer { lastDelivery = now }
        guard pendingWords > 0, let lastDelivery else { return }
        let deliveryGap = min(2.4, max(0.4, now.timeIntervalSince(lastDelivery)))
        let target = min(240.0, max(120.0, deliveryGap * 1_000 / Double(pendingWords)))
        milliseconds = milliseconds * 0.75 + target * 0.25
    }

    mutating func reset() {
        milliseconds = 180.0
        lastDelivery = nil
    }
}

/// Streams the first translation word by word, then holds later generations
/// until they are complete so a revised translation can replace its changed
/// words without briefly collapsing back to a one-word prefix.
struct RevisableCaption {
    private(set) var displayTokens: [CaptionToken] = []
    private var hypothesis: [String] = []
    private var consumed = 0
    private var captionID = ""
    private var generation = -1
    private var nextTokenID = 0

    var hasPending: Bool { consumed < hypothesis.count }
    var pendingCount: Int { max(0, hypothesis.count - consumed) }
    var text: String { displayTokens.map(\.text).joined().trimmingCharacters(in: .whitespacesAndNewlines) }

    mutating func update(_ text: String, id: String, generation nextGeneration: Int, final: Bool) -> CaptionRevision? {
        let incoming = ProgressiveCaption.words(text)
        guard !incoming.isEmpty else { return nil }
        if id != captionID {
            captionID = id
            generation = nextGeneration
            hypothesis = incoming
            consumed = 0
            displayTokens.removeAll()
            return nil
        }
        if nextGeneration == generation {
            hypothesis = incoming
            consumed = min(consumed, hypothesis.count)
            return nil
        }
        guard nextGeneration > generation else { return nil }
        // A model restart begins again at the first token. Keep the readable
        // prior result until the replacement is complete.
        guard final else { return nil }
        generation = nextGeneration
        hypothesis = incoming
        consumed = incoming.count
        return reconcile(with: incoming)
    }

    mutating func advanceStep() -> CaptionStep? {
        guard hasPending else { return nil }
        let startsCaption = displayTokens.isEmpty
        if !displayTokens.isEmpty, consumed > 0,
           hypothesis[consumed - 1].last?.isWhitespace == true,
           displayTokens[displayTokens.count - 1].text.last?.isWhitespace != true {
            displayTokens[displayTokens.count - 1].text += " "
        }
        let token = CaptionToken(id: nextTokenID, text: hypothesis[consumed])
        nextTokenID += 1
        displayTokens.append(token)
        consumed += 1
        return CaptionStep(fullText: text, appendedToken: token, visibleTokens: displayTokens, startsCaption: startsCaption)
    }

    private mutating func reconcile(with incoming: [String]) -> CaptionRevision {
        let previous = displayTokens
        let oldKeys = previous.map { Self.key($0.text) }
        let newKeys = incoming.map(Self.key)
        let matches = Self.longestCommonSubsequence(oldKeys, newKeys)
        var idsByNewIndex: [Int: Int] = [:]
        for (oldIndex, newIndex) in matches { idsByNewIndex[newIndex] = previous[oldIndex].id }
        var revised: [CaptionToken] = []
        for (index, word) in incoming.enumerated() {
            let id: Int
            if let existing = idsByNewIndex[index] { id = existing }
            else { id = nextTokenID; nextTokenID += 1 }
            revised.append(CaptionToken(id: id, text: word))
        }
        displayTokens = revised
        return CaptionRevision(previousTokenIDs: Set(previous.map(\.id)), visibleTokens: revised, startsCaption: false)
    }

    private static func key(_ word: String) -> String {
        word.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: nil)
            .trimmingCharacters(in: .whitespacesAndNewlines.union(.punctuationCharacters))
    }

    private static func longestCommonSubsequence(_ old: [String], _ new: [String]) -> [(Int, Int)] {
        guard !old.isEmpty, !new.isEmpty else { return [] }
        var lengths = Array(repeating: Array(repeating: 0, count: new.count + 1), count: old.count + 1)
        for i in stride(from: old.count - 1, through: 0, by: -1) {
            for j in stride(from: new.count - 1, through: 0, by: -1) {
                lengths[i][j] = old[i] == new[j] ? lengths[i + 1][j + 1] + 1 : max(lengths[i + 1][j], lengths[i][j + 1])
            }
        }
        var matches: [(Int, Int)] = [], i = 0, j = 0
        while i < old.count, j < new.count {
            if old[i] == new[j] { matches.append((i, j)); i += 1; j += 1 }
            else if lengths[i + 1][j] >= lengths[i][j + 1] { i += 1 }
            else { j += 1 }
        }
        return matches
    }
}

/// Freezes words once read; recognition may revise only the unpublished suffix.
struct ProgressiveCaption {
    private(set) var visible: [String] = []
    private var visibleIDs: [Int] = []
    private var hypothesis: [String] = []
    private var consumed = 0
    private var captionID = ""
    private var replaceOnNextWord = false
    private var nextTokenID = 0
    var hasPending: Bool { consumed < hypothesis.count }
    var pendingCount: Int { max(0, hypothesis.count - consumed) }
    var text: String { visible.joined().trimmingCharacters(in: .whitespacesAndNewlines) }
    var displayTokens: [CaptionToken] { zip(visibleIDs, visible).map { CaptionToken(id: $0.0, text: $0.1) } }

    mutating func update(_ text: String, id: String) {
        let incoming = Self.words(text)
        guard !incoming.isEmpty else { return }
        if id != captionID {
            captionID = id
            hypothesis = incoming
            consumed = 0
            replaceOnNextWord = true
        } else {
            // A new generation streams the same sentence again from its start.
            // Keep our read boundary until that stream catches up.
            if incoming.count < consumed {
                hypothesis = Array(hypothesis.prefix(consumed))
                return
            }
            consumed = Self.mapBoundary(from: hypothesis, to: incoming, consumed: consumed)
            hypothesis = incoming
        }
    }
    mutating func advanceStep() -> CaptionStep? {
        guard hasPending else { return nil }
        var startsCaption = false
        if replaceOnNextWord { visible.removeAll(); visibleIDs.removeAll(); replaceOnNextWord = false; startsCaption = true }
        if consumed > 0, let end = hypothesis[consumed - 1].trimmingCharacters(in: .whitespacesAndNewlines).last,
           ".!?。！？؟".contains(end) {
            visible.removeAll(); visibleIDs.removeAll(); startsCaption = true
        } else if !visible.isEmpty {
            var previous = visible[visible.count - 1].trimmingCharacters(in: .whitespacesAndNewlines)
            if let end = previous.last, ".!?。！？؟".contains(end) {
                previous.removeLast()
                visible[visible.count - 1] = previous
            }
        }
        if !visible.isEmpty, consumed > 0,
           hypothesis[consumed - 1].last?.isWhitespace == true,
           visible[visible.count - 1].last?.isWhitespace != true {
            visible[visible.count - 1] += " "
        }
        let appendedText = hypothesis[consumed]
        visible.append(appendedText)
        let appendedToken = CaptionToken(id: nextTokenID, text: appendedText)
        visibleIDs.append(nextTokenID)
        nextTokenID += 1
        consumed += 1
        return CaptionStep(fullText: text, appendedToken: appendedToken, visibleTokens: displayTokens, startsCaption: startsCaption)
    }
    mutating func advance() -> String? { advanceStep()?.fullText }
    static func words(_ text: String) -> [String] {
        let tokenizer = NLTokenizer(unit: .word)
        tokenizer.string = text
        var starts: [String.Index] = []
        tokenizer.enumerateTokens(in: text.startIndex..<text.endIndex) { range, _ in
            starts.append(range.lowerBound); return true
        }
        guard !starts.isEmpty else { return text.isEmpty ? [] : [text] }
        starts[0] = text.startIndex
        return starts.indices.map { index in
            String(text[starts[index]..<(index + 1 < starts.count ? starts[index + 1] : text.endIndex)])
        }
    }
    private static func key(_ word: String) -> String {
        word.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: nil)
            .trimmingCharacters(in: .whitespacesAndNewlines.union(.punctuationCharacters))
    }
    /// Align hypotheses so inserted/deleted words don't cause suffix duplication.
    private static func mapBoundary(from old: [String], to new: [String], consumed: Int) -> Int {
        guard consumed > 0 else { return 0 }
        let a = old.map(key), b = new.map(key)
        var costs = Array(repeating: Array(repeating: 0, count: b.count + 1), count: a.count + 1)
        for i in 0...a.count { costs[i][0] = i }
        for j in 0...b.count { costs[0][j] = j }
        if !a.isEmpty && !b.isEmpty {
            for i in 1...a.count {
                for j in 1...b.count {
                    costs[i][j] = min(costs[i-1][j-1] + (a[i-1] == b[j-1] ? 0 : 1), min(costs[i-1][j] + 1, costs[i][j-1] + 1))
                }
            }
        }
        var steps: [(Int, Int)] = []
        var i = a.count, j = b.count
        while i > 0 || j > 0 {
            if i > 0 && j > 0 && costs[i][j] == costs[i-1][j-1] + (a[i-1] == b[j-1] ? 0 : 1) {
                steps.append((1, 1)); i -= 1; j -= 1
            } else if i > 0 && costs[i][j] == costs[i-1][j] + 1 {
                steps.append((1, 0)); i -= 1
            } else { steps.append((0, 1)); j -= 1 }
        }
        var oldIndex = 0, newIndex = 0
        for step in steps.reversed() {
            oldIndex += step.0; newIndex += step.1
            if oldIndex >= consumed { return newIndex }
        }
        return newIndex
    }
}
