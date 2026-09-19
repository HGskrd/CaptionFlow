import XCTest
@testable import CaptionFlow

final class ProgressiveCaptionTests: XCTestCase {
    func testBatchAppearsOneWordPerTick() {
        var caption = ProgressiveCaption()
        caption.update("The train leaves tomorrow.", id: "one")
        XCTAssertEqual(caption.advance(), "The")
        XCTAssertEqual(caption.advance(), "The train")
        XCTAssertEqual(caption.advance(), "The train leaves")
        XCTAssertEqual(caption.advance(), "The train leaves tomorrow.")
        XCTAssertNil(caption.advance())
    }
    func testStepExposesOnlyTheNewWordChunk() {
        var caption = ProgressiveCaption()
        caption.update("The train leaves", id: "one")
        XCTAssertEqual(caption.advanceStep(), CaptionStep(fullText: "The", appendedText: "The "))
        XCTAssertEqual(caption.advanceStep(), CaptionStep(fullText: "The train", appendedText: "train "))
    }
    func testRevisionDoesNotRewriteReadWords() {
        var caption = ProgressiveCaption()
        caption.update("The train leaves soon", id: "one")
        _ = caption.advance(); _ = caption.advance()
        caption.update("The tram leaves tomorrow", id: "one")
        XCTAssertEqual(caption.advance(), "The train leaves")
        XCTAssertEqual(caption.advance(), "The train leaves tomorrow")
    }
    func testInsertionsAndDeletionsDoNotReplayWords() {
        var caption = ProgressiveCaption()
        caption.update("I like trains", id: "one")
        for _ in 0..<3 { _ = caption.advance() }
        caption.update("I really like trains today", id: "one")
        XCTAssertEqual(caption.advance(), "I like trains today")
        caption.update("I like trains today very much", id: "one")
        XCTAssertEqual(caption.advance(), "I like trains today very")
        XCTAssertEqual(caption.advance(), "I like trains today very much")
    }
    func testTranslationRegenerationDoesNotBlankOrReplay() {
        var caption = ProgressiveCaption()
        caption.update("Bonjour le monde", id: "one")
        for _ in 0..<3 { _ = caption.advance() }
        caption.update("Bonjour", id: "one")
        XCTAssertNil(caption.advance())
        caption.update("Bonjour le", id: "one")
        // A restarted token stream must not replay a prefix already on screen.
        XCTAssertNil(caption.advance())
        caption.update("Bonjour le monde entier", id: "one")
        XCTAssertEqual(caption.advance(), "Bonjour le monde entier")
    }
    func testNewSentenceReplacesOnlyOnFirstWordAndCJKPreservesSpacing() {
        var caption = ProgressiveCaption()
        caption.update("Old sentence.", id: "one")
        _ = caption.advance(); _ = caption.advance()
        caption.update("New sentence.", id: "two")
        XCTAssertEqual(caption.visible.joined(), "Old sentence.")
        XCTAssertEqual(caption.advance(), "New")
        XCTAssertEqual(ProgressiveCaption.words("你好世界。").joined(), "你好世界。")
    }
    func testSentenceBoundaryDoesNotDependOnProvisionalPunctuation() {
        var caption = ProgressiveCaption()
        caption.update("The train.", id: "one")
        _ = caption.advance(); _ = caption.advance()
        caption.update("The train leaves soon. Please wait.", id: "one")
        XCTAssertEqual(caption.advance(), "The train leaves")
        XCTAssertEqual(caption.advance(), "The train leaves soon.")
        XCTAssertEqual(caption.advance(), "Please")
        XCTAssertEqual(caption.advance(), "Please wait.")
    }
}
