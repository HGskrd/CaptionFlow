import XCTest
@testable import CaptionFlow

final class RevisableCaptionTests: XCTestCase {
    func testFirstTranslationStreamsOneWordAtATime() {
        var caption = RevisableCaption()
        XCTAssertNil(caption.update("The value chain", id: "one", generation: 1, final: true))
        XCTAssertEqual(caption.advanceStep()?.fullText, "The")
        XCTAssertEqual(caption.advanceStep()?.fullText, "The value")
        XCTAssertEqual(caption.advanceStep()?.fullText, "The value chain")
    }

    func testNewGenerationWaitsThenRewritesChangedLanguage() {
        var caption = RevisableCaption()
        _ = caption.update("La chaîne de valeur", id: "one", generation: 1, final: true)
        while caption.advanceStep() != nil {}
        XCTAssertNil(caption.update("The ", id: "one", generation: 2, final: false))
        XCTAssertEqual(caption.text, "La chaîne de valeur")
        let revision = caption.update("The value chain", id: "one", generation: 2, final: true)
        XCTAssertEqual(revision?.visibleTokens.map(\.text).joined(), "The value chain")
        XCTAssertEqual(caption.text, "The value chain")
    }

    func testRevisionKeepsIdentityForUnchangedWords() {
        var caption = RevisableCaption()
        _ = caption.update("The old value chain", id: "one", generation: 1, final: true)
        while caption.advanceStep() != nil {}
        let oldChainID = caption.displayTokens.last?.id
        let revision = caption.update("The global value chain", id: "one", generation: 2, final: true)
        XCTAssertEqual(revision?.visibleTokens.last?.id, oldChainID)
    }
}
