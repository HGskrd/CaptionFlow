import XCTest
@testable import CaptionFlow

final class CaptionPacerTests: XCTestCase {
    func testIrregularBatchesChangeCadenceGraduallyWithinReadableBounds() {
        var pacer = CaptionPacer()
        let start = Date(timeIntervalSince1970: 100)
        pacer.observe(pendingWords: 2, at: start)
        XCTAssertEqual(pacer.milliseconds, 180)

        pacer.observe(pendingWords: 2, at: start.addingTimeInterval(0.6))
        XCTAssertEqual(pacer.milliseconds, 195)

        pacer.observe(pendingWords: 12, at: start.addingTimeInterval(1.0))
        XCTAssertEqual(pacer.milliseconds, 176.25)
        XCTAssertGreaterThanOrEqual(pacer.milliseconds, 120)
        XCTAssertLessThanOrEqual(pacer.milliseconds, 240)
    }

    func testResetRestoresNeutralCadenceForANewUtterance() {
        var pacer = CaptionPacer()
        let start = Date(timeIntervalSince1970: 100)
        pacer.observe(pendingWords: 1, at: start)
        pacer.observe(pendingWords: 1, at: start.addingTimeInterval(2))
        XCTAssertGreaterThan(pacer.milliseconds, 180)
        pacer.reset()
        XCTAssertEqual(pacer.milliseconds, 180)
    }
}
