import XCTest
import Darwin
@testable import CaptionFlow

private final class BridgeEvents: @unchecked Sendable {
    private let lock = NSLock()
    private var events: [[String: Any]] = []
    func append(_ event: [String: Any]) { lock.lock(); events.append(event); lock.unlock() }
    var snapshot: [[String: Any]] { lock.lock(); defer { lock.unlock() }; return events }
}

final class WorkerBridgeTests: XCTestCase {
    func testBlockedPipePreservesEveryFrameAndStopFollowsAudio() async throws {
        let bridge = WorkerBridge()
        defer { bridge.stop() }
        let events = BridgeEvents()
        bridge.onEvent = { events.append($0) }
        try bridge.start(configuration: ["type": "configure"], executable: URL(fileURLWithPath: "/bin/sh"),
                         arguments: ["-c", "/bin/sleep 0.1; exec /bin/cat"])
        let frames = (0..<64).map { Data(repeating: UInt8($0), count: 16000) }
        frames.forEach { bridge.sendAudio($0) }
        try await bridge.finish(timeout: 5)
        let received = events.snapshot
        XCTAssertEqual(received.first?["type"] as? String, "configure")
        XCTAssertEqual(received.last?["type"] as? String, "stop")
        XCTAssertEqual(received.compactMap { ($0["pcm"] as? String).flatMap { Data(base64Encoded: $0) } }, frames)
        bridge.sendAudio(Data([255])) // Capture callbacks arriving after stop cannot reopen the pipe.
        XCTAssertEqual(events.snapshot.count, 66)
    }

    func testAudioOverflowReportsAnErrorInsteadOfOverwritingSpeech() async throws {
        let bridge = WorkerBridge(maximumAudioBytes: 8)
        defer { bridge.stop() }
        let events = BridgeEvents()
        bridge.onEvent = { events.append($0) }
        try bridge.start(configuration: ["type": "configure"], executable: URL(fileURLWithPath: "/bin/cat"), arguments: [])
        bridge.sendAudio(Data(repeating: 1, count: 16))
        bridge.sendAudio(Data(repeating: 2, count: 16))
        try await bridge.finish(timeout: 5)
        let errors = events.snapshot.filter { $0["type"] as? String == "error" }
        XCTAssertEqual(errors.count, 1)
        XCTAssertTrue((errors.first?["message"] as? String)?.contains("avoid losing speech") == true)
    }

    func testUnresponsiveWorkerHasABoundedDrainTimeout() async throws {
        let bridge = WorkerBridge()
        defer { bridge.stop() }
        try bridge.start(configuration: ["type": "configure"], executable: URL(fileURLWithPath: "/bin/sleep"), arguments: ["10"])
        let start = Date()
        do {
            try await bridge.finish(timeout: 0.05)
            XCTFail("Expected a drain timeout")
        } catch { XCTAssertTrue(error.localizedDescription.contains("did not finish draining")) }
        XCTAssertLessThan(Date().timeIntervalSince(start), 2)
    }

    func testFailedWorkerCannotReportASuccessfulDrain() async throws {
        let bridge = WorkerBridge()
        defer { bridge.stop() }
        try bridge.start(configuration: ["type": "configure"], executable: URL(fileURLWithPath: "/bin/sh"),
                         arguments: ["-c", "read line; exit 3"])
        do {
            try await bridge.finish(timeout: 5)
            XCTFail("Expected a worker exit error")
        } catch { XCTAssertTrue(error.localizedDescription.contains("before shutdown completed (3)")) }
    }

    func testDrainTimeoutReapsAWorkerThatIgnoresTermination() async throws {
        let bridge = WorkerBridge()
        defer { bridge.stop() }
        let events = BridgeEvents()
        let ready = expectation(description: "Signal handler installed")
        bridge.onEvent = {
            events.append($0)
            if $0["type"] as? String == "ready" { ready.fulfill() }
        }
        try bridge.start(configuration: ["type": "configure"], executable: URL(fileURLWithPath: "/bin/sh"),
                         arguments: ["-c", "trap '' TERM; printf '{\"type\":\"ready\",\"pid\":%d}\\n' $$; exec /bin/sleep 10"])
        await fulfillment(of: [ready], timeout: 2)
        let pid = pid_t(try XCTUnwrap(events.snapshot.first?["pid"] as? Int))
        do {
            try await bridge.finish(timeout: 0.05)
            XCTFail("Expected a drain timeout")
        } catch { XCTAssertTrue(error.localizedDescription.contains("did not finish draining")) }
        let alive = kill(pid, 0)
        let error = errno
        XCTAssertEqual(alive, -1)
        XCTAssertEqual(error, ESRCH)
    }

    func testRuntimeReadinessTracksRequirementsAndInterpreter() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let runtime = root.appendingPathComponent("runtime")
        try FileManager.default.createDirectory(at: runtime.appendingPathComponent("bin"), withIntermediateDirectories: true)
        let python = runtime.appendingPathComponent("bin/python3")
        try FileManager.default.createSymbolicLink(at: python, withDestinationURL: URL(fileURLWithPath: "/bin/cat"))
        let requirements = root.appendingPathComponent("requirements.txt")
        try Data("dependency==1\n".utf8).write(to: requirements)
        try Data().write(to: runtime.appendingPathComponent(".ready-v2"))
        XCTAssertFalse(WorkerBridge.runtimeIsInstalled(at: runtime, requirements: requirements))
        let marker = runtime.appendingPathComponent(".requirements-sha256")
        try WorkerBridge.requirementsFingerprint(requirements).write(to: marker)
        XCTAssertTrue(WorkerBridge.runtimeIsInstalled(at: runtime, requirements: requirements))
        try Data("dependency==2\n".utf8).write(to: requirements)
        XCTAssertFalse(WorkerBridge.runtimeIsInstalled(at: runtime, requirements: requirements))
        try WorkerBridge.requirementsFingerprint(requirements).write(to: marker)
        try FileManager.default.removeItem(at: python)
        XCTAssertFalse(WorkerBridge.runtimeIsInstalled(at: runtime, requirements: requirements))
    }
}
