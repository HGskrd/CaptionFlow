import XCTest
@testable import CaptionFlow

final class LiveCaptionTests: XCTestCase {
    func testDefaultsAndOldPreferencesRemainDecodable() throws {
        var prefs = Preferences(); prefs.captionFlow = .time; prefs.captionTimeLimit = 20
        let restored = try JSONDecoder().decode(Preferences.self, from: JSONEncoder().encode(prefs))
        XCTAssertEqual(restored.effectiveCaptionPresentation, .rolling)
        XCTAssertEqual(restored.effectiveTranslationPresentation, .phrase)
        XCTAssertEqual(restored.effectiveCaptionVisibleLines, 3)
        XCTAssertEqual(restored.effectiveCaptionClearAfter, 0)
        XCTAssertTrue(restored.effectiveCaptionGentleAnimation)
        XCTAssertEqual(restored.effectiveCaptionFlow, .time)
        prefs.captionPresentation = .transcript; prefs.captionVisibleLines = 4; prefs.captionClearAfter = 10
        prefs.captionGentleAnimation = false
        let updated = try JSONDecoder().decode(Preferences.self, from: JSONEncoder().encode(prefs))
        XCTAssertEqual(updated.effectiveCaptionPresentation, .transcript)
        XCTAssertEqual(updated.effectiveCaptionVisibleLines, 4)
        XCTAssertEqual(updated.effectiveCaptionClearAfter, 10)
        XCTAssertFalse(updated.effectiveCaptionGentleAnimation)
    }

    func testCorrectionsReplaceRatherThanDuplicateAndRetainSuffixIdentity() {
        var document = LiveCaptionDocument()
        document.update("Send 15 euros tomorrow.", id: "a", revision: 1, final: false)
        let old = document.tokens
        document.update("Send 50 euros tomorrow.", id: "a", revision: 2, final: true)
        XCTAssertEqual(document.tokens.map(\.text).joined(), "Send 50 euros tomorrow.")
        XCTAssertEqual(document.tokens.last?.id, old.last?.id)
        document.update("wrong stale result", id: "a", revision: 1, final: true)
        XCTAssertEqual(document.tokens.map(\.text).joined(), "Send 50 euros tomorrow.")
    }

    func testPhraseKeepsPreviousTranslationUntilReplacementCompletes() {
        var document = LiveCaptionDocument()
        document.update("The meeting starts now.", id: "a", revision: 1, final: true, holdRevisions: true)
        document.update("La", id: "a", revision: 2, final: false, holdRevisions: true)
        XCTAssertEqual(document.phrase.map(\.text).joined(), "The meeting starts now.")
        document.update("La réunion commence.", id: "a", revision: 2, final: true, holdRevisions: true)
        XCTAssertEqual(document.phrase.map(\.text).joined(), "La réunion commence.")
        document.update("Next", id: "b", revision: 3, final: false)
        XCTAssertEqual(document.phrase.map(\.text).joined(), "La réunion commence.")
    }

    func testSilenceClearsWholeCaptionButPreservesHistoryAndRespectsSpeech() {
        var document = LiveCaptionDocument(); let start = Date(timeIntervalSince1970: 100)
        document.update("One two three.", id: "a", revision: 0, final: true, at: start)
        document.clearIfIdle(seconds: 5, lastSpeech: start.addingTimeInterval(4), now: start.addingTimeInterval(6))
        XCTAssertFalse(document.visibleTokens(for: .rolling).isEmpty)
        document.clearIfIdle(seconds: 5, lastSpeech: start, now: start.addingTimeInterval(6))
        XCTAssertTrue(document.visibleTokens(for: .rolling).isEmpty)
        XCTAssertFalse(document.visibleTokens(for: .transcript).isEmpty)
        document.update("New speech.", id: "b", revision: 1, final: true, at: start.addingTimeInterval(7))
        XCTAssertEqual(document.visibleTokens(for: .rolling).map(\.text).joined(), "New speech.")
        document.restore()
        XCTAssertTrue(document.visibleTokens(for: .rolling).map(\.text).joined().contains("One two three."))
    }

    func testLineStartsSurviveAppendAndHistoryEvictionWithoutRemeasuring() {
        var layout = CaptionLineLayout(); var calls = 0
        let measure: (String) -> Double = { text in calls += 1; return Double(text.count) }
        let tokens = (0..<8).map { CaptionToken(id: $0, text: "word ") }
        layout.update(tokens: Array(tokens.prefix(5)), width: 10, fontKey: "test", measure: measure)
        let firstIDs = layout.lines.map(\.id); let firstCalls = calls
        layout.update(tokens: tokens, width: 10, fontKey: "test", measure: measure)
        XCTAssertEqual(Array(layout.lines.prefix(3)).map(\.id), firstIDs)
        XCTAssertEqual(calls, firstCalls)
        layout.update(tokens: Array(tokens.dropFirst()), width: 10, fontKey: "test", measure: measure)
        XCTAssertEqual(layout.lines.dropFirst().compactMap { $0.words.first?.id }, ["2:0", "4:0", "6:0"])
        XCTAssertEqual(layout.lines.last?.words.map(\.text).joined(), "word word ")
    }

    func testCorrectingFirstWordAndExtendingDoesNotRecreateReadLines() {
        var document = LiveCaptionDocument(); var layout = CaptionLineLayout()
        document.update("He sent fifteen euros to Paris", id: "a", revision: 0, final: false)
        let original = document.tokens
        layout.update(tokens: original, width: 30, fontKey: "test", measure: { Double($0.count) })
        let firstRowID = layout.lines.first?.id
        document.update("She sent fifty euros to Paris yesterday.", id: "a", revision: 1, final: true)
        XCTAssertEqual(Array(document.tokens.prefix(original.count)).map(\.id), original.map(\.id))
        layout.update(tokens: document.tokens, width: 30, fontKey: "test", measure: { Double($0.count) })
        XCTAssertEqual(layout.lines.first?.id, firstRowID)
        XCTAssertEqual(document.tokens.map(\.text).joined(), "She sent fifty euros to Paris yesterday.")
    }

    func testHistoryBoundAndLongWords() {
        var document = LiveCaptionDocument()
        for index in 0..<250 { document.update("Sentence \(index).", id: "\(index)", revision: index, final: true) }
        XCTAssertEqual(document.utterances.count, 200)
        XCTAssertEqual(document.utterances.last?.id, "249")
        var layout = CaptionLineLayout()
        layout.update(tokens: [CaptionToken(id: 1, text: "👩🏽‍💻👩🏽‍💻👩🏽‍💻")], width: 1, fontKey: "test", measure: { Double($0.count) })
        XCTAssertEqual(layout.lines.count, 3)
        XCTAssertEqual(layout.lines.flatMap(\.words).map(\.text).joined(), "👩🏽‍💻👩🏽‍💻👩🏽‍💻")
    }
}
