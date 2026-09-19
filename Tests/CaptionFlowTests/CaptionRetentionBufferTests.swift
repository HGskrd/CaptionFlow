import XCTest
@testable import CaptionFlow

final class CaptionRetentionBufferTests: XCTestCase {
    func testWordLimitDropsOneOldWordAtATime() {
        var buffer = CaptionRetentionBuffer()
        buffer.merge(step(0, "One ")); buffer.merge(step(1, "two ")); buffer.merge(step(2, "three "))
        XCTAssertEqual(buffer.text(mode: .words, wordLimit: 3, timeLimit: 6), "One two three")
        buffer.merge(step(3, "four "))
        XCTAssertEqual(buffer.text(mode: .words, wordLimit: 3, timeLimit: 6), "two three four")
    }

    func testTimeLimitExpiresWordsIndividually() {
        var buffer = CaptionRetentionBuffer()
        let start = Date(timeIntervalSince1970: 100)
        buffer.merge(step(0, "One "), at: start)
        buffer.merge(step(1, "two "), at: start.addingTimeInterval(1))
        XCTAssertEqual(buffer.text(mode: .time, wordLimit: 20, timeLimit: 2, now: start.addingTimeInterval(2.5)), "two")
        XCTAssertEqual(buffer.text(mode: .time, wordLimit: 20, timeLimit: 2, now: start.addingTimeInterval(3.1)), "")
    }

    func testLaterHypothesisRestoresSpacingWithoutReplacingIdentity() {
        var buffer = CaptionRetentionBuffer()
        buffer.merge(step(0, "people"))
        let first = CaptionToken(id: 0, text: "people ")
        let second = CaptionToken(id: 1, text: "models")
        buffer.merge(CaptionStep(fullText: "people models", appendedToken: second, visibleTokens: [first, second], startsCaption: false))
        let tokens = buffer.tokens(mode: .words, wordLimit: 18, timeLimit: 6)
        XCTAssertEqual(tokens.map(\.id), [0, 1])
        XCTAssertEqual(tokens.map(\.text).joined(), "people models")
    }

    func testNewCaptionGetsASeparatorButCJKDoesNot() {
        var latin = CaptionRetentionBuffer()
        latin.merge(step(0, "métiers."))
        latin.merge(step(1, "Je", startsCaption: true))
        XCTAssertEqual(latin.text(mode: .words, wordLimit: 18, timeLimit: 6), "métiers. Je")

        var cjk = CaptionRetentionBuffer()
        cjk.merge(step(0, "世界。"))
        cjk.merge(step(1, "你好", startsCaption: true))
        XCTAssertEqual(cjk.text(mode: .words, wordLimit: 18, timeLimit: 6), "世界。你好")
    }

    private func step(_ id: Int, _ text: String, startsCaption: Bool = false) -> CaptionStep {
        let token = CaptionToken(id: id, text: text)
        return CaptionStep(fullText: text.trimmingCharacters(in: .whitespaces), appendedToken: token, visibleTokens: [token], startsCaption: startsCaption)
    }
}
