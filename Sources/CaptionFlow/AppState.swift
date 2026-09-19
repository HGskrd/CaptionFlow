import SwiftUI
import AppKit

@MainActor
final class AppState: ObservableObject {
    @Published var preferences = Preferences.load() { didSet { preferences.save() } }
    @Published var inspector = true
    @Published var fullScreenInspector = false
    @Published var tab = "Style"
    @Published var phase = "Ready when you are"
    @Published var busy = false
    @Published var listening = false
    @Published var installing = false
    @Published var installed = WorkerBridge.isInstalled
    @Published var scanning = false
    @Published var models: [LocalModel] = []
    @Published var scanMessage = ""
    @Published var original = ""
    @Published var translations: [String: String] = [:]
    @Published var detectedLanguage = ""
    @Published var error: String?
    @Published var level: Float = 0
    @Published var cachedTokens = 0
    @Published var latency = 0.0
    @Published var preview = false
    private var worker: WorkerBridge?
    private var audio: AudioCapture?
    private var captureTask: Task<Void, Never>?
    private var session = UUID()
    private var revision = -1
    var active: Bool { busy || listening }

    func install() {
        guard !installing else { return }
        installing = true; error = nil; phase = "Setting up local inference…"
        Task {
            do {
                try await WorkerBridge.install(using: preferences.pythonPath) { [weak self] message in
                    Task { @MainActor in self?.phase = message }
                }
                installed = true; phase = "Local inference is ready"
            } catch { self.error = error.localizedDescription; phase = "Setup needs attention" }
            installing = false
        }
    }
    func start() {
        guard !active, !installing else { return }
        guard installed else { inspector = true; tab = "Models"; phase = "Set up local inference to begin"; return }
        if preferences.displayMode != .original && preferences.targets.isEmpty {
            error = "Choose at least one translation language, or display Original only."; return
        }
        error = nil; original = ""; translations = [:]; preview = false; cachedTokens = 0; revision = -1
        busy = true; phase = "Loading local models…"; session = UUID()
        let id = session; let bridge = WorkerBridge(); worker = bridge
        bridge.onEvent = { [weak self] event in Task { @MainActor in guard self?.session == id else { return }; self?.receive(event) } }
        bridge.onExit = { [weak self] message in Task { @MainActor in guard let self, self.session == id, self.active else { return }; self.fail(message) } }
        do {
            try bridge.start(configuration: [
                "type": "configure", "speech_model": preferences.whisperModel,
                "translation_model": preferences.translationModel, "targets": preferences.displayMode == .original ? [] : preferences.targets,
                "source_language": preferences.sourceLanguage, "allow_downloads": preferences.allowDownloads,
                "cache_dir": WorkerBridge.support.appendingPathComponent("models").path,
                "silence_threshold": preferences.silenceThreshold
            ])
        } catch { fail(error.localizedDescription) }
    }
    private func receive(_ event: [String: Any]) {
        switch event["type"] as? String {
        case "status": phase = event["message"] as? String ?? phase
        case "ready":
            phase = "Starting audio…"
            let capture = AudioCapture(); audio = capture
            let id = session; let bridge = worker
            capture.onAudio = { [weak self, weak bridge] data, level in
                bridge?.sendAudio(data)
                Task { @MainActor in if self?.session == id { self?.level = level } }
            }
            capture.onError = { [weak self] message in Task { @MainActor in if self?.session == id { self?.fail(message) } } }
            let source = preferences.source
            captureTask = Task {
                do {
                    try await capture.start(source)
                    guard session == id, !Task.isCancelled else { await capture.stop(); return }
                    busy = false; listening = true; phase = "Listening · on device"
                } catch {
                    await capture.stop()
                    if session == id { fail(error.localizedDescription) }
                }
            }
        case "source":
            let next = event["revision"] as? Int ?? 0
            guard next >= revision else { return }
            revision = next; original = event["text"] as? String ?? ""; translations = [:]
            detectedLanguage = (event["language"] as? String ?? "").uppercased()
            latency = event["latency"] as? Double ?? 0
        case "translation":
            guard event["revision"] as? Int == revision, let language = event["language"] as? String else { return }
            translations[language] = event["text"] as? String ?? ""
            cachedTokens = event["cached_tokens"] as? Int ?? 0
        case "error": fail(event["message"] as? String ?? "Local inference failed.")
        default: break
        }
    }
    private func fail(_ message: String) { stop(); error = message; phase = "Needs attention" }
    func stop() {
        session = UUID(); captureTask?.cancel(); captureTask = nil
        worker?.stop(); worker = nil
        let oldAudio = audio; audio = nil
        busy = false; listening = false; level = 0; phase = "Stopped"
        Task { await oldAudio?.stop() }
    }
    func showPreview() {
        guard !active else { return }
        preview = true; original = "A little more understanding, one sentence at a time."
        translations = ["French": "Un peu plus de compréhension, une phrase à la fois.", "Spanish": "Un poco más de comprensión, una frase a la vez.", "German": "Ein bisschen mehr Verständnis, Satz für Satz."]
        phase = "Style preview · sample text"
    }
    func addRoots() {
        let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.allowsMultipleSelection = true
        panel.message = "Choose model folders or cache roots. Hugging Face snapshots and nested model folders are discovered automatically."
        if panel.runModal() == .OK { addRoots(panel.urls) }
    }
    func addRoots(_ urls: [URL]) {
        for url in urls where (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true {
            if !preferences.roots.contains(url.path) { preferences.roots.append(url.path) }
        }
        scan()
    }
    func scan() {
        guard !scanning else { return }
        scanning = true; let roots = preferences.roots + [WorkerBridge.support.appendingPathComponent("models").path]
        Task {
            let result = await Task.detached { ModelDiscovery.scan(roots: roots.filter { FileManager.default.fileExists(atPath: $0) }) }.value
            models = result.models; scanMessage = "\(models.count) model folders found" + (result.warnings.isEmpty ? "" : " · " + result.warnings.joined(separator: "; "))
            scanning = false
        }
    }
}
