import XCTest
@testable import CaptionFlow

final class RuntimeArchitectureTests: XCTestCase {
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
