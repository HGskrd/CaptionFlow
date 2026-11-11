import XCTest
import AppKit
import SwiftUI
@testable import CaptionFlow

final class TranscriptScrollTests: XCTestCase {
    func testPassageMappingUsesIdentityAndWithinPassageProgress() {
        let source = [TranscriptSpan(captionID: "a", start: 0, length: 100), TranscriptSpan(captionID: "b", start: 100, length: 200)]
        let translation = [TranscriptSpan(captionID: "a", start: 0, length: 400), TranscriptSpan(captionID: "b", start: 400, length: 100)]
        let anchor = TranscriptScrollMapping.anchor(at: 200, spans: source)!
        XCTAssertEqual(anchor.captionID, "b")
        XCTAssertEqual(anchor.progress, 0.5)
        XCTAssertEqual(TranscriptScrollMapping.position(for: anchor, spans: translation, timeline: ["a", "b"]), 450)
    }

    func testPendingTranslationAndEvictedHistoryHaveDeterministicFallbacks() {
        let spans = [TranscriptSpan(captionID: "a", start: 0, length: 20), TranscriptSpan(captionID: "c", start: 20, length: 80)]
        let waiting = TranscriptAnchor(captionID: "b", progress: 0.5)
        XCTAssertEqual(TranscriptScrollMapping.position(for: waiting, spans: spans, timeline: ["a", "b", "c"]), 0)
        let arrived = spans + [TranscriptSpan(captionID: "b", start: 10, length: 10)]
        XCTAssertEqual(TranscriptScrollMapping.position(for: waiting, spans: arrived, timeline: ["a", "b", "c"]), 15)
        XCTAssertEqual(TranscriptScrollMapping.position(for: waiting, spans: spans, timeline: ["c"]), 20)
        XCTAssertNil(TranscriptScrollMapping.anchor(at: 0, spans: []))
        XCTAssertNil(TranscriptScrollMapping.position(for: waiting, spans: [], timeline: ["a", "b"]))
    }

    func testDocumentPreservesSharedIDsAndUnicodeParagraphs() {
        var document = LiveCaptionDocument()
        document.update("👩🏽‍💻 Bonjour.\nDeuxième ligne.", id: "source-1", revision: 0, final: true)
        document.update("日本語", id: "source-2", revision: 1, final: true)
        XCTAssertEqual(document.transcriptSegments.map(\.id), ["source-1", "source-2"])
        XCTAssertEqual(document.transcriptSegments[0].text, "👩🏽‍💻 Bonjour.\nDeuxième ligne.")
    }

    func testNativeLinkedScrollPreservesPassageDuringUpdatesAndResize() async {
        await MainActor.run {
            _ = NSApplication.shared
            var position = TranscriptScrollPosition()
            let binding = Binding(get: { position }, set: { position = $0 })
            var source = (0..<30).map { TranscriptSegment(id: "\($0)", text: "Source \($0). " + String(repeating: "Short words. ", count: 3)) }
            let translated = (0..<30).map { TranscriptSegment(id: "\($0)", text: "Traduction \($0). " + String(repeating: "Une phrase beaucoup plus longue. ", count: 5)) }
            let first = CaptionTranscript.makeScrollView(); first.setFrameSize(NSSize(width: 420, height: 190))
            let second = CaptionTranscript.makeScrollView(); second.setFrameSize(NSSize(width: 420, height: 190))
            let a = CaptionTranscript.Coordinator(); a.attach(first)
            let b = CaptionTranscript.Coordinator(); b.attach(second)
            func render() {
                let timeline = source.map(\.id)
                a.update(CaptionTranscript(segments: source, timeline: timeline, font: .systemFont(ofSize: 28), color: .textColor, position: binding))
                b.update(CaptionTranscript(segments: translated, timeline: timeline, font: .systemFont(ofSize: 28), color: .textColor, position: binding))
            }
            render()
            XCTAssertEqual(a.spans.count, 30); XCTAssertEqual(b.spans.count, 30)
            // Exercise the same notification path as a wheel or scrollbar drag.
            first.interaction?(true)
            first.contentView.scroll(to: NSPoint(x: 0, y: a.spans[12].start))
            first.interaction?(false)
            XCTAssertFalse(position.followsLive); XCTAssertEqual(position.anchor?.captionID, "12")
            render()
            XCTAssertEqual(TranscriptScrollMapping.anchor(at: second.contentView.bounds.minY, spans: b.spans)?.captionID, "12")
            second.interaction?(true)
            second.contentView.scroll(to: NSPoint(x: 0, y: b.spans[7].start + b.spans[7].length * 0.5))
            second.interaction?(false)
            render()
            XCTAssertEqual(TranscriptScrollMapping.anchor(at: first.contentView.bounds.minY, spans: a.spans)?.captionID, "7")
            source[2] = TranscriptSegment(id: "2", text: String(repeating: "A corrected earlier passage. ", count: 30))
            source.append(TranscriptSegment(id: "30", text: "New live text.")); render()
            first.setFrameSize(NSSize(width: 680, height: 190)); render()
            XCTAssertEqual(TranscriptScrollMapping.anchor(at: first.contentView.bounds.minY, spans: a.spans)?.captionID, "7")
            XCTAssertEqual(TranscriptScrollMapping.anchor(at: second.contentView.bounds.minY, spans: b.spans)?.captionID, "7")
            position = TranscriptScrollPosition(); render()
            XCTAssertTrue(position.followsLive)
            XCTAssertGreaterThan(first.contentView.bounds.minY, a.spans[7].start)
            XCTAssertGreaterThan(second.contentView.bounds.minY, b.spans[7].start)
        }
    }
}
