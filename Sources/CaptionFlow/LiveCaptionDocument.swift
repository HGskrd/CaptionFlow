import Foundation

/// Bounded, memory-only scrollback. Model revisions replace an utterance instead
/// of appending duplicates. Presentation never queues words behind the decoder.
struct LiveCaptionDocument {
    let sessionID = UUID()
    struct Utterance {
        var id: String
        var revision: Int
        var tokens: [CaptionToken]
    }
    private(set) var utterances: [Utterance] = []
    private(set) var phrase: [CaptionToken] = []
    private(set) var lastChange = Date.distantPast
    private var nextID = 0
    private var phraseCaptionID = ""
    private var phraseRevision = -1
    private(set) var cleared = false
    private var hiddenTokenIDs = Set<Int>()
    static let maximumTokens = 10_000

    var tokens: [CaptionToken] {
        utterances.enumerated().flatMap { index, utterance in
            var words = utterance.tokens
            if index < utterances.count - 1, !words.isEmpty {
                words[words.count - 1].text = words.last!.text.trimmingCharacters(in: .whitespacesAndNewlines) + "\n"
            }
            return words
        }
    }

    mutating func update(_ text: String, id: String, revision: Int, final: Bool, holdRevisions: Bool = false, at now: Date = Date()) {
        let words = ProgressiveCaption.words(text)
        guard !words.isEmpty else { return }
        let index = utterances.firstIndex { $0.id == id }
        if let index, revision < utterances[index].revision { return }
        if holdRevisions, let index, revision > utterances[index].revision, !final { return }
        let old = index.map { utterances[$0].tokens } ?? []
        // Preserve unchanged prefix/suffix identities, including punctuation-only
        // changes. Changed words are always shown; stability never hides a correction.
        var prefix = 0
        while prefix < min(old.count, words.count), old[prefix].text.trimmingCharacters(in: .whitespacesAndNewlines) == words[prefix].trimmingCharacters(in: .whitespacesAndNewlines) { prefix += 1 }
        var suffix = 0
        while suffix < min(old.count, words.count) - prefix,
              old[old.count - 1 - suffix].text == words[words.count - 1 - suffix] { suffix += 1 }
        var identities: [Int: Int] = [:]
        for index in 0..<prefix { identities[index] = old[index].id }
        if suffix > 0 {
            for offset in 0..<suffix { identities[words.count - suffix + offset] = old[old.count - suffix + offset].id }
        }
        let oldMiddle = Array(old[prefix..<(old.count - suffix)])
        let newMiddle = Array(words[prefix..<(words.count - suffix)])
        if !oldMiddle.isEmpty && !newMiddle.isEmpty {
            // Preserve identity for edits as well as matches, so correcting a
            // name never recreates the entire row or fades words already read.
            if oldMiddle.count * newMiddle.count <= 40_000 {
                var oldIndex = 0; var newIndex = 0
                for (oldCount, newCount) in ProgressiveCaption.alignment(from: oldMiddle.map(\.text), to: newMiddle) {
                    if oldCount > 0 && newCount > 0 { identities[prefix + newIndex] = oldMiddle[oldIndex].id }
                    oldIndex += oldCount; newIndex += newCount
                }
            } else {
                for index in 0..<min(oldMiddle.count, newMiddle.count) { identities[prefix + index] = oldMiddle[index].id }
            }
        }
        let revised = words.enumerated().map { offset, word -> CaptionToken in
            if let identity = identities[offset] { return CaptionToken(id: identity, text: word) }
            let token = CaptionToken(id: nextID, text: word); nextID += 1; return token
        }
        if old != revised { lastChange = now; cleared = false }
        let utterance = Utterance(id: id, revision: revision, tokens: revised)
        if let index { utterances[index] = utterance } else { utterances.append(utterance) }
        // Keep a previous translation readable while its next generation starts
        // again from one word. Commit a new generation only when complete.
        if !holdRevisions || id != phraseCaptionID || revision == phraseRevision || final {
            let boundary = revised.lastIndex { token in
                token.text.trimmingCharacters(in: .whitespacesAndNewlines).last.map { ".!?;:。！？؟".contains($0) } ?? false
            }
            let count = final ? revised.count : boundary.map { $0 + 1 } ?? (revised.count / 8 * 8)
            if count > 0 {
                let nextPhrase = Array(revised.prefix(count))
                if phrase != nextPhrase { lastChange = now; cleared = false }
                phrase = nextPhrase; phraseCaptionID = id; phraseRevision = revision
            }
        }
        var total = utterances.reduce(0) { $0 + $1.tokens.count }
        while utterances.count > 1 && (utterances.count > 200 || total > Self.maximumTokens) {
            total -= utterances.removeFirst().tokens.count
        }
        if utterances.count == 1, utterances[0].tokens.count > Self.maximumTokens {
            utterances[0].tokens = Array(utterances[0].tokens.suffix(Self.maximumTokens))
        }
        if phrase.count > Self.maximumTokens { phrase = Array(phrase.suffix(Self.maximumTokens)) }
    }

    mutating func restore() { cleared = false; hiddenTokenIDs = [] }

    mutating func clearIfIdle(seconds: Int, lastSpeech: Date, now: Date = Date()) {
        if seconds > 0, now.timeIntervalSince(max(lastChange, lastSpeech)) >= Double(seconds) {
            cleared = true; hiddenTokenIDs = Set((tokens + phrase).map(\.id))
        }
    }

    func visibleTokens(for mode: CaptionPresentation) -> [CaptionToken] {
        // Scrollback is never erased by the silence preference.
        if mode == .transcript { return tokens }
        if cleared { return [] }
        return (mode == .phrase ? phrase : tokens).filter { !hiddenTokenIDs.contains($0.id) }
    }
}

/// Keeps existing line starts across appends and bounded-history eviction. Width
/// measurements are cached; appending words does not measure the history again.
struct CaptionLineLayout {
    struct Word: Identifiable, Equatable {
        var id: String
        var text: String
        var width: Double
    }
    struct Line: Identifiable, Equatable {
        var words: [Word]
        var id: String
    }
    private(set) var lines: [Line] = []
    private var measured: [String: Double] = [:]
    private var previousWidth: Double = 0
    private var previousFont = ""
    private var nextLineID = 0

    mutating func update(tokens: [CaptionToken], width: Double, fontKey: String, measure: (String) -> Double) {
        guard width > 0 else { lines = []; return }
        if fontKey != previousFont { measured = [:] }
        let starts = width == previousWidth && fontKey == previousFont ? Set(lines.dropFirst().compactMap { $0.words.first?.id }) : []
        previousWidth = width; previousFont = fontKey
        var rows: [Line] = []; var row: [Word] = []; var used = 0.0
        func size(_ text: String) -> Double {
            if let cached = measured[text] { return cached }
            let result = ceil(measure(text)); measured[text] = result; return result
        }
        func flush() { if !row.isEmpty { rows.append(Line(words: row, id: "")); row = []; used = 0 } }
        func append(_ text: String, id: String) {
            let ink = size(text.trimmingCharacters(in: .whitespacesAndNewlines))
            if !row.isEmpty && (starts.contains(id) || used + ink > width) { flush() }
            let advance = size(text)
            row.append(Word(id: id, text: text, width: advance)); used += advance
        }
        for token in tokens {
            let text = token.text.replacingOccurrences(of: "\n", with: " ")
            if size(text.trimmingCharacters(in: .whitespacesAndNewlines)) > width {
                var part = ""; var fragment = 0
                for character in text {
                    if !part.isEmpty && size(part + String(character)) > width {
                        append(part, id: "\(token.id):\(fragment)"); fragment += 1; part = ""
                    }
                    part.append(character)
                }
                if !part.isEmpty { append(part, id: "\(token.id):\(fragment)") }
            } else { append(text, id: "\(token.id):0") }
            if token.text.contains("\n") { flush() }
        }
        flush()
        let oldRows = Dictionary(uniqueKeysWithValues: lines.flatMap { line in line.words.map { ($0.id, line.id) } })
        var usedRows = Set<String>()
        for index in rows.indices {
            var overlaps: [String: Int] = [:]
            for word in rows[index].words {
                if let id = oldRows[word.id], !usedRows.contains(id) { overlaps[id, default: 0] += 1 }
            }
            if let id = overlaps.max(by: { $0.value < $1.value })?.key { rows[index].id = id }
            else { rows[index].id = "line-\(nextLineID)"; nextLineID += 1 }
            usedRows.insert(rows[index].id)
        }
        lines = rows
        if measured.count > 20_000 { measured = [:] }
    }
}
