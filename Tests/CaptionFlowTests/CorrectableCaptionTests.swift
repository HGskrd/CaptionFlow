import XCTest
@testable import CaptionFlow

final class CorrectableCaptionTests: XCTestCase {
    func testCorrectionRewritesReadWordsAndPacesNewWords() throws {
        var caption = ProgressiveCaption()
        caption.update("The tram leaves tomorrow", id: "one", allowCorrections: true)
        _ = caption.advance(); _ = caption.advance()
        let ids = caption.displayTokens.map(\.id)
        let correction = try XCTUnwrap(caption.update("The train leaves tomorrow morning", id: "one", allowCorrections: true))
        XCTAssertEqual(caption.text, "The train")
        XCTAssertEqual(caption.displayTokens.map(\.id), ids)
        XCTAssertEqual(correction.visibleTokens.count, 2)
        XCTAssertEqual(caption.advance(), "The train leaves")
        XCTAssertEqual(caption.advance(), "The train leaves tomorrow")
        XCTAssertEqual(caption.advance(), "The train leaves tomorrow morning")
        XCTAssertNil(caption.advance())
    }

    func testReadInsertionsAndDeletionsDoNotReplayTail() {
        var caption = ProgressiveCaption()
        caption.update("I like trains today", id: "one", allowCorrections: true)
        for _ in 0..<3 { _ = caption.advance() }
        caption.update("I really like trains today", id: "one", allowCorrections: true)
        XCTAssertEqual(caption.text, "I really like trains")
        caption.update("I like trains today", id: "one", allowCorrections: true)
        XCTAssertEqual(caption.text, "I like trains")
        XCTAssertEqual(caption.advance(), "I like trains today")
        caption.update("I like trains", id: "one", allowCorrections: true)
        XCTAssertEqual(caption.text, "I like trains")
        XCTAssertNil(caption.advance())
    }

    func testCorrectedSentenceBoundaryAndNewUtterance() {
        var caption = ProgressiveCaption()
        caption.update("The tram. Please wait", id: "one", allowCorrections: true)
        while caption.advance() != nil {}
        XCTAssertEqual(caption.text, "Please wait")
        caption.update("The train. Please wait here", id: "one", allowCorrections: true)
        XCTAssertEqual(caption.text, "Please wait")
        XCTAssertEqual(caption.advance(), "Please wait here")
        caption.update("Next utterance", id: "two", allowCorrections: true)
        caption.update("Next corrected utterance", id: "two", allowCorrections: true)
        XCTAssertEqual(caption.text, "Please wait here")
        XCTAssertEqual(caption.advance(), "Next")
        XCTAssertEqual(caption.advance(), "Next corrected")
    }

    func testLateCorrectionsPreserveWordAndTimeWindows() throws {
        var caption = ProgressiveCaption(), buffer = CaptionRetentionBuffer()
        let date = Date(timeIntervalSince1970: 100)
        caption.update("The tram leaves tomorrow", id: "one", allowCorrections: true)
        while let step = caption.advanceStep() { buffer.merge(step, at: date) }
        XCTAssertEqual(buffer.text(mode: .words, wordLimit: 2, timeLimit: 6), "leaves tomorrow")
        let correction = try XCTUnwrap(caption.update("The train departs tomorrow", id: "one", allowCorrections: true))
        buffer.reconcile(correction, at: date.addingTimeInterval(1), preservingWindow: true)
        XCTAssertEqual(buffer.text(mode: .words, wordLimit: 2, timeLimit: 6), "departs tomorrow")
        XCTAssertEqual(buffer.text(mode: .time, wordLimit: 20, timeLimit: 2, now: date.addingTimeInterval(2.1)), "")
        let late = try XCTUnwrap(caption.update("The train departs today", id: "one", allowCorrections: true))
        buffer.reconcile(late, at: date.addingTimeInterval(3), preservingWindow: true)
        XCTAssertTrue(buffer.isEmpty)
    }

    func testOlderPreferencesLoadAndDelaySettingsRoundTrip() throws {
        let encoder = JSONEncoder(), decoder = JSONDecoder()
        let data = try encoder.encode(Preferences())
        let legacy = try decoder.decode(Preferences.self, from: data)
        XCTAssertFalse(legacy.effectiveVoxtralDualDelay)
        XCTAssertEqual(legacy.effectiveVoxtralDelayMS, 480)
        var preferences = legacy
        preferences.voxtralDualDelay = true
        XCTAssertEqual(preferences.effectiveVoxtralDelayMS, 240)
        XCTAssertEqual(preferences.effectiveVoxtralCorrectionDelayMS, 1200)
        preferences.voxtralDelayMS = 1200
        XCTAssertEqual(preferences.effectiveVoxtralCorrectionDelayMS, 2400)
        preferences.voxtralDelayMS = 2400
        XCTAssertEqual(preferences.effectiveVoxtralDelayMS, 240)
        let restored = try decoder.decode(Preferences.self, from: encoder.encode(preferences))
        XCTAssertTrue(restored.effectiveVoxtralDualDelay)
        XCTAssertEqual(restored.voxtralDelayMS, 2400)
    }

    func testCorrectionCanInsertBeforeFirstRetainedWordWithoutResettingExpiry() throws {
        var caption = ProgressiveCaption(), buffer = CaptionRetentionBuffer()
        let date = Date(timeIntervalSince1970: 100)
        caption.update("I like trains", id: "one", allowCorrections: true)
        while let step = caption.advanceStep() { buffer.merge(step, at: date) }
        let correction = try XCTUnwrap(caption.update("Well I like trains", id: "one", allowCorrections: true))
        buffer.reconcile(correction, at: date.addingTimeInterval(1), preservingWindow: true)
        XCTAssertEqual(buffer.text(mode: .words, wordLimit: 20, timeLimit: 6), "Well I like trains")
        XCTAssertEqual(buffer.text(mode: .time, wordLimit: 20, timeLimit: 2, now: date.addingTimeInterval(2.1)), "")
    }
}
