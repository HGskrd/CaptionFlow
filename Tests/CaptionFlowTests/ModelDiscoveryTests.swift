import XCTest
@testable import CaptionFlow

final class ModelDiscoveryTests: XCTestCase {
    func testNestedSnapshotAndDirectModelsDeduplicate() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let speech = root.appendingPathComponent("models--org--speech/snapshots/commit")
        let translation = root.appendingPathComponent("other/translator")
        let pytorch = root.appendingPathComponent("unsupported")
        for dir in [speech, translation, pytorch] { try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true) }
        try Data(#"{"n_mels":80,"n_audio_ctx":1500}"#.utf8).write(to: speech.appendingPathComponent("config.json"))
        try Data().write(to: speech.appendingPathComponent("weights.npz"))
        try Data(#"{"model_type":"qwen2"}"#.utf8).write(to: translation.appendingPathComponent("config.json"))
        try Data().write(to: translation.appendingPathComponent("model.safetensors"))
        try Data("{}".utf8).write(to: translation.appendingPathComponent("tokenizer_config.json"))
        try Data(#"{"model_type":"whisper"}"#.utf8).write(to: pytorch.appendingPathComponent("config.json"))
        try Data().write(to: pytorch.appendingPathComponent("pytorch_model.bin"))
        let result = ModelDiscovery.scan(roots: [root.path, speech.path])
        XCTAssertEqual(result.models.count, 2)
        XCTAssertEqual(result.models.first(where: { $0.kind == .speech })?.name, "org/speech")
        let discovered = try XCTUnwrap(result.models.first(where: { $0.kind == .translation }))
        XCTAssertEqual(URL(fileURLWithPath: discovered.path).resolvingSymlinksInPath(), translation.resolvingSymlinksInPath())
    }
    func testPreferencesRoundTrip() throws {
        var prefs = Preferences()
        prefs.targets = ["French", "Japanese"]
        prefs.roots = ["/a cache/with spaces", "/another"]
        prefs.displayMode = .translated
        prefs.allowDownloads = false
        let decoded = try JSONDecoder().decode(Preferences.self, from: JSONEncoder().encode(prefs))
        XCTAssertEqual(decoded.targets, prefs.targets)
        XCTAssertEqual(decoded.roots, prefs.roots)
        XCTAssertEqual(decoded.displayMode, .translated)
        XCTAssertFalse(decoded.allowDownloads)
    }
}
