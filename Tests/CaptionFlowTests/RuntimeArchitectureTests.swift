import XCTest
@testable import CaptionFlow

final class RuntimeArchitectureTests: XCTestCase {
    func testBundledPythonIsFoundAfterRelocatingTheApp() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let resources = root.appendingPathComponent("CaptionFlow.app/Contents/Resources")
        let executable = resources.appendingPathComponent("python/bin/python3")
        XCTAssertNil(WorkerBridge.bundledPython(in: resources))
        try FileManager.default.createDirectory(at: executable.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data("#!/bin/sh\n".utf8).write(to: executable)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executable.path)
        XCTAssertEqual(WorkerBridge.bundledPython(in: resources), executable)
        let relocated = root.appendingPathComponent("Installed CaptionFlow.app")
        try FileManager.default.moveItem(at: root.appendingPathComponent("CaptionFlow.app"), to: relocated)
        XCTAssertEqual(WorkerBridge.bundledPython(in: relocated.appendingPathComponent("Contents/Resources")),
                       relocated.appendingPathComponent("Contents/Resources/python/bin/python3"))
    }

    func testRuntimeConfigurationMatchesAppArchitecture() {
        var preferences = Preferences()
        preferences.whisperModel = "saved-mlx-speech"
        preferences.translationModel = "saved-mlx-translation"
        #if arch(arm64)
        XCTAssertTrue(Preferences.supportsMLX)
        XCTAssertEqual(preferences.effectiveSpeechModel, "saved-mlx-speech")
        XCTAssertEqual(preferences.effectiveTranslationModel, "saved-mlx-translation")
        #else
        XCTAssertFalse(Preferences.supportsMLX)
        XCTAssertEqual(preferences.effectiveSpeechModel, Preferences.nemotronSpeechModel)
        XCTAssertEqual(preferences.effectiveTranslationModel, "tencent/Hy-MT2-1.8B-GGUF")
        XCTAssertTrue(WorkerBridge.runtime.path.hasSuffix("runtime-x86_64"))
        #endif
    }
}
