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

    func testRecognizesParakeetVoxtralAndQwenASRAsSpeechModels() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let parakeet = root.appendingPathComponent("parakeet")
        let voxtral = root.appendingPathComponent("voxtral")
        let qwenASR = root.appendingPathComponent("qwen-asr")
        for model in [parakeet, voxtral, qwenASR] {
            try FileManager.default.createDirectory(at: model, withIntermediateDirectories: true)
        }
        try Data(#"{"target":"nemo.collections.asr.models.rnnt_bpe_models.EncDecRNNTBPEModel","preprocessor":{},"encoder":{},"decoding":{"model_type":"tdt"}}"#.utf8).write(to: parakeet.appendingPathComponent("config.json"))
        try Data().write(to: parakeet.appendingPathComponent("model.safetensors"))
        try Data(#"{"model_type":"voxtral_realtime","encoder_args":{}}"#.utf8).write(to: voxtral.appendingPathComponent("config.json"))
        try Data().write(to: voxtral.appendingPathComponent("model.safetensors"))
        try Data(#"{"model_type":"qwen3_asr"}"#.utf8).write(to: qwenASR.appendingPathComponent("config.json"))
        try Data().write(to: qwenASR.appendingPathComponent("model.safetensors"))

        let result = ModelDiscovery.scan(roots: [root.path])
        XCTAssertEqual(Set(result.models.filter { $0.kind == .speech }.map(\.name)), ["parakeet", "voxtral", "qwen-asr"])
    }
    func testCompactSpeechArchivesAreDiscoverable() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        for (name, type, weights) in [("redux", "parakeet_tdt", "model.safetensors"), ("phonon", "parakeet_tdt_five_value", "phonon-2.5.bps.tar.zst")] {
            let folder = root.appendingPathComponent(name)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try JSONSerialization.data(withJSONObject: ["model_type": type]).write(to: folder.appendingPathComponent("config.json"))
            try Data().write(to: folder.appendingPathComponent(weights))
        }
        XCTAssertEqual(Set(ModelDiscovery.scan(roots: [root.path]).models.map(\.name)), ["redux", "phonon"])
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
