import SwiftUI
import AppKit

struct RGBA: Codable, Equatable {
    var r: Double; var g: Double; var b: Double; var a: Double = 1
    var color: Color { Color(.sRGB, red: r, green: g, blue: b, opacity: a) }
    init(_ r: Double, _ g: Double, _ b: Double) { self.r = r; self.g = g; self.b = b }
    init(_ color: Color) {
        let c = NSColor(color).usingColorSpace(.sRGB) ?? .white
        r = c.redComponent; g = c.greenComponent; b = c.blueComponent; a = c.alphaComponent
    }
}
enum DisplayMode: String, Codable, CaseIterable { case original = "Original", translated = "Translation", both = "Both" }
enum AudioSource: String, Codable, CaseIterable { case mixed = "Microphone + Mac audio", microphone = "Microphone", system = "Mac audio" }
enum CaptionFlowMode: String, Codable, CaseIterable {
    case sentence = "Sentence"
    case words = "Words"
    case time = "Time"
}
struct Preferences: Codable {
    static let qwen3ASRSpeechModel = "mlx-community/Qwen3-ASR-1.7B-4bit"
    static let qwen25TranslationModel = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"
    static let qwen35TranslationModel = "mlx-community/Qwen3.5-0.8B-MLX-4bit"
    static let qwen35TwoBTranslationModel = "mlx-community/Qwen3.5-2B-MLX-4bit"
    static let hyMT2TranslationModel = "mlx-community/Hy-MT2-1.8B-4bit"
    var typeface = "System"
    var fontSize = 54.0
    var foreground = RGBA(0.96, 0.96, 0.94)
    var background = RGBA(0.065, 0.075, 0.085)
    var displayMode = DisplayMode.both
    var source = AudioSource.mixed
    var sourceLanguage = "auto"
    var targets = ["French"]
    var roots: [String] = []
    var whisperModel = "mlx-community/whisper-small-mlx"
    var translationModel = Self.qwen25TranslationModel
    var allowDownloads = true
    var pythonPath = "/opt/homebrew/bin/python3.11"
    var silenceThreshold = 0.008
    // Optional storage keeps preferences written by older builds decodable.
    var voxtralDualDelay: Bool?
    var voxtralDelayMS: Int?
    var voxtralCorrectionDelayMS: Int?
    static let voxtralDelays = Array(stride(from: 80, through: 1200, by: 80)) + [2400]
    var effectiveVoxtralDualDelay: Bool { voxtralDualDelay ?? false }
    var effectiveVoxtralDelayMS: Int {
        let value = voxtralDelayMS ?? (effectiveVoxtralDualDelay ? 240 : 480)
        return Self.voxtralDelays.contains(value) && (!effectiveVoxtralDualDelay || value < 2400)
            ? value : (effectiveVoxtralDualDelay ? 240 : 480)
    }
    var effectiveVoxtralCorrectionDelayMS: Int {
        let value = voxtralCorrectionDelayMS ?? 1200
        return Self.voxtralDelays.contains(value) && value > effectiveVoxtralDelayMS
            ? value : (Self.voxtralDelays.first { $0 > effectiveVoxtralDelayMS } ?? 2400)
    }
    var captionFlow: CaptionFlowMode?
    var captionWordLimit: Int?
    var captionTimeLimit: Double?
    var effectiveCaptionFlow: CaptionFlowMode { captionFlow ?? .sentence }
    var effectiveCaptionWordLimit: Int { min(60, max(3, captionWordLimit ?? 18)) }
    var effectiveCaptionTimeLimit: Double { min(20, max(1, captionTimeLimit ?? 6)) }
    static let languages = ["English", "French", "Spanish", "German", "Italian", "Portuguese", "Dutch", "Polish", "Russian", "Ukrainian", "Arabic", "Hindi", "Chinese", "Japanese", "Korean"]
    static func load() -> Self {
        guard let data = UserDefaults.standard.data(forKey: "preferences"), let value = try? JSONDecoder().decode(Self.self, from: data) else { return Self() }
        return value
    }
    func save() { if let data = try? JSONEncoder().encode(self) { UserDefaults.standard.set(data, forKey: "preferences") } }
}
struct LocalModel: Identifiable, Sendable {
    enum Kind: String, Sendable { case speech, translation }
    var id: String { path }
    let path: String
    let name: String
    let kind: Kind
}
struct ScanResult: Sendable { var models: [LocalModel]; var warnings: [String] }
enum ModelDiscovery {
    static func isSpeechModel(config: [String: Any]) -> Bool {
        if config["n_mels"] != nil && config["n_audio_ctx"] != nil { return true }
        if ["voxtral_realtime", "qwen3_asr"].contains(config["model_type"] as? String ?? "") { return true }
        if let target = config["target"] as? String, target.localizedCaseInsensitiveContains("EncDecRNNT") { return true }
        if let decoding = config["decoding"] as? [String: Any],
           ["tdt", "rnnt", "ctc"].contains(decoding["model_type"] as? String ?? ""),
           config["preprocessor"] != nil,
           config["encoder"] != nil { return true }
        return false
    }
    static func scan(roots: [String]) -> ScanResult {
        var models: [LocalModel] = []; var seen = Set<String>(); var warnings: [String] = []
        let fm = FileManager.default
        for root in roots {
            let url = URL(fileURLWithPath: root)
            guard let enumerator = fm.enumerator(at: url, includingPropertiesForKeys: [.isDirectoryKey], options: [.skipsPackageDescendants], errorHandler: { _, _ in true }) else {
                warnings.append("Cannot read \(url.lastPathComponent)"); continue
            }
            var count = 0
            for case let file as URL in enumerator {
                count += 1
                if count > 100_000 { warnings.append("Scan limit reached in \(url.lastPathComponent). Choose a more specific folder."); break }
                if [".git", "blobs", ".venv", "node_modules"].contains(file.lastPathComponent) { enumerator.skipDescendants(); continue }
                guard file.lastPathComponent == "config.json",
                      let data = try? Data(contentsOf: file),
                      let config = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
                let folder = file.deletingLastPathComponent()
                let canonical = folder.resolvingSymlinksInPath().path
                guard seen.insert(canonical).inserted,
                      let contents = try? fm.contentsOfDirectory(atPath: folder.path),
                      contents.contains(where: { $0.hasSuffix(".safetensors") || $0.hasSuffix(".npz") }) else { continue }
                let kind: LocalModel.Kind
                if isSpeechModel(config: config) { kind = .speech }
                else if config["model_type"] != nil && contents.contains("tokenizer_config.json") { kind = .translation }
                else { continue }
                let parent = folder.deletingLastPathComponent()
                let name = parent.lastPathComponent == "snapshots" ? parent.deletingLastPathComponent().lastPathComponent.replacingOccurrences(of: "models--", with: "").replacingOccurrences(of: "--", with: "/") : folder.lastPathComponent
                models.append(LocalModel(path: folder.path, name: name, kind: kind))
            }
        }
        return ScanResult(models: models.sorted { $0.name < $1.name }, warnings: warnings)
    }
}
