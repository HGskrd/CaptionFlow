import XCTest
@testable import CaptionFlow

final class CaptionRetentionBufferTests: XCTestCase {
    func testWordLimitDropsOneOldWordAtATime() {
        var buffer = CaptionRetentionBuffer()
        buffer.append("One "); buffer.append("two "); buffer.append("three ")
        XCTAssertEqual(buffer.text(mode: .words, wordLimit: 3, timeLimit: 6), "One two three")
        buffer.append("four ")
        XCTAssertEqual(buffer.text(mode: .words, wordLimit: 3, timeLimit: 6), "two three four")
    }

    func testTimeLimitExpiresWordsIndividually() {
        var buffer = CaptionRetentionBuffer()
        let start = Date(timeIntervalSince1970: 100)
        buffer.append("One ", at: start)
        buffer.append("two ", at: start.addingTimeInterval(1))
        XCTAssertEqual(buffer.text(mode: .time, wordLimit: 20, timeLimit: 2, now: start.addingTimeInterval(2.5)), "two")
        XCTAssertEqual(buffer.text(mode: .time, wordLimit: 20, timeLimit: 2, now: start.addingTimeInterval(3.1)), "")
    }
}
