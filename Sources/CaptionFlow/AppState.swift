import SwiftUI
import AppKit
import CoreGraphics

@MainActor
final class AppState: ObservableObject {
    let community = CommunitySupport()
    @Published var preferences = Preferences.load() {
        didSet {
            preferences.save()
            if oldValue.effectiveCaptionClearAfter != preferences.effectiveCaptionClearAfter {
                if preferences.effectiveCaptionClearAfter == 0 {
                    originalDocument.restore()
                    for language in translationDocuments.keys { translationDocuments[language]?.restore() }
                }
                startLiveClearTimer()
            }
            if oldValue.effectiveCaptionFlow != preferences.effectiveCaptionFlow ||
                oldValue.effectiveCaptionWordLimit != preferences.effectiveCaptionWordLimit ||
                oldValue.effectiveCaptionTimeLimit != preferences.effectiveCaptionTimeLimit {
                applyCaptionFlowPreferences()
            }
        }
    }
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
    @Published var originalDocument = LiveCaptionDocument()
    @Published var translationDocuments: [String: LiveCaptionDocument] = [:]
    private var lastSpeech = Date.distantPast
    private var previewTask: Task<Void, Never>?
    private var liveClearTask: Task<Void, Never>?
    @Published var originalTokens: [CaptionToken] = []
    @Published var translationTokens: [String: [CaptionToken]] = [:]
    @Published var detectedLanguage = ""
    @Published var error: String?
    @Published var capturePermissionIssue = false
    @Published var level: Float = 0
    @Published var cachedTokens = 0
    @Published var latency = 0.0
    @Published var preview = false
    private var worker: WorkerBridge?
    private var audio: AudioCapture?
    private var captureTask: Task<Void, Never>?
    private var session = UUID()
    private var revision = -1
    private var captionID = ""
    private var originalStream = ProgressiveCaption()
    private var translationStreams: [String: RevisableCaption] = [:]
    private var originalRetention = CaptionRetentionBuffer()
    private var translationRetentions: [String: CaptionRetentionBuffer] = [:]
    private var originalPacer = CaptionPacer()
    private var translationPacers: [String: CaptionPacer] = [:]
    private var playbackTask: Task<Void, Never>?
    private var expirationTask: Task<Void, Never>?
    var active: Bool { busy || listening }
    var usesVoxtral: Bool {
        if preferences.whisperModel.localizedCaseInsensitiveContains("voxtral") { return true }
        let configURL = URL(fileURLWithPath: preferences.whisperModel).appendingPathComponent("config.json")
        guard let data = try? Data(contentsOf: configURL),
              let config = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return false }
        return config["model_type"] as? String == "voxtral_realtime"
    }
    var speechModelNote: String {
        let model = preferences.whisperModel.lowercased()
        if model.contains("nemotron") { return "Nemotron 3.5 · 0.6B · native streaming · Metal" }
        if model.contains("redux") { return "Parakeet Redux · compressed 0.6B · 25 European languages · chunked" }
        if model.contains("phonon") { return "Phonon 2.5 · compressed 0.6B · 25 European languages · chunked" }
        if model.contains("parakeet") { return "Parakeet · 0.6B · fast · 25 European languages" }
        if model.contains("qwen3-asr") { return "Qwen3-ASR · 1.7B · 4-bit · 30 languages and 22 Chinese dialects · snapshot live" }
        if model.contains("voxtral") { return "Voxtral Realtime · 4B · 4-bit · 13 languages · higher memory use" }
        return "Whisper Small · multilingual · balanced"
    }
    var translationModelNote: String {
        if preferences.translationModel == Preferences.hyMT2TranslationModel {
            return "Hy-MT2 · about 1.02 GB · translation-specific · 33 languages"
        }
        if preferences.translationModel == Preferences.qwen35TwoBTranslationModel {
            return "Qwen 3.5 · about 1.7 GB · larger model · partial revisions decode fresh"
        }
        if preferences.translationModel == Preferences.qwen35TranslationModel {
            return "Qwen 3.5 · about 652 MB · lower memory use · partial revisions decode fresh"
        }
        return "Qwen 2.5 · about 1.1 GB · reusable prefix cache"
    }

    func install() {
        guard !installing else { return }
        guard community.enabled else {
            error = "Local-only mode disables runtime and model downloads. Supply an offline runtime, or enable online participation under Privacy / sharing."
            return
        }
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
        capturePermissionIssue = false
        if preferences.source != .microphone && !CGPreflightScreenCaptureAccess() {
            capturePermissionIssue = true
            error = "macOS has not authorized this build of CaptionFlow to capture Mac audio. If the switch is already enabled, the saved permission may refer to an older build. Remove the old CaptionFlow entry and add this app again in Screen & System Audio Recording, then quit and reopen CaptionFlow."
            return
        }
        guard installed else { inspector = true; tab = "Models"; phase = "Set up local inference to begin"; return }
        if preferences.displayMode != .original && preferences.targets.isEmpty {
            error = "Choose at least one translation language, or display Original only."; return
        }
        resetPlayback()
        originalDocument = LiveCaptionDocument(); translationDocuments = [:]
        lastSpeech = .distantPast
        error = nil; original = ""; translations = [:]; originalTokens = []; translationTokens = [:]; preview = false; cachedTokens = 0; revision = -1
        busy = true; phase = "Loading local models…"; session = UUID()
        let id = session; let bridge = WorkerBridge(); worker = bridge
        bridge.onEvent = { [weak self] event in Task { @MainActor in guard self?.session == id else { return }; self?.receive(event) } }
        bridge.onExit = { [weak self] message in Task { @MainActor in guard let self, self.session == id, self.active else { return }; self.fail(message) } }
        do {
            try bridge.start(configuration: [
                "type": "configure", "speech_model": preferences.whisperModel,
                "translation_model": preferences.translationModel, "targets": preferences.displayMode == .original ? [] : preferences.targets,
                "source_language": preferences.sourceLanguage, "allow_downloads": preferences.allowDownloads && community.enabled,
                "cache_dir": WorkerBridge.support.appendingPathComponent("models").path,
                "silence_threshold": preferences.silenceThreshold,
                "voxtral_dual_delay": preferences.effectiveVoxtralDualDelay,
                "voxtral_delay_ms": preferences.effectiveVoxtralDelayMS,
                "voxtral_correction_delay_ms": preferences.effectiveVoxtralCorrectionDelayMS
            ])
        } catch { fail(error.localizedDescription) }
    }
    func openCaptureSettings() {
        error = nil
        CGRequestScreenCaptureAccess()
        NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")!)
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
                Task { @MainActor in
                    if let self, self.session == id {
                        self.level = level
                        if Double(level) >= self.preferences.silenceThreshold { self.lastSpeech = Date() }
                    }
                }
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
            revision = next
            let incomingCaptionID = event["caption_id"] as? String ?? "utterance"
            if incomingCaptionID != captionID { originalPacer.reset() }
            captionID = incomingCaptionID
            originalDocument.update(event["text"] as? String ?? "", id: captionID,
                                    revision: next, final: event["final"] as? Bool ?? false)
            startLiveClearTimer()
            if let correction = originalStream.update(event["text"] as? String ?? "", id: captionID,
                                                      allowCorrections: event["revisable"] as? Bool ?? false) {
                originalRetention.reconcile(correction, preservingWindow: true)
                let tokens = preferences.effectiveCaptionFlow == .sentence
                    ? originalStream.displayTokens
                    : originalRetention.tokens(mode: preferences.effectiveCaptionFlow,
                                               wordLimit: preferences.effectiveCaptionWordLimit,
                                               timeLimit: preferences.effectiveCaptionTimeLimit)
                originalTokens = tokens
                original = tokens.map(\.text).joined().trimmingCharacters(in: .whitespacesAndNewlines)
            }
            originalPacer.observe(pendingWords: originalStream.pendingCount)
            playPendingWords()
            detectedLanguage = (event["language"] as? String ?? "").uppercased()
            latency = event["latency"] as? Double ?? 0
        case "translation":
            guard let translationRevision = event["revision"] as? Int,
                  let language = event["language"] as? String else { return }
            let eventCaptionID = event["caption_id"] as? String ?? captionID
            guard eventCaptionID == captionID else { return }
            translationDocuments[language, default: LiveCaptionDocument()].update(
                event["text"] as? String ?? "", id: eventCaptionID, revision: translationRevision,
                final: event["final"] as? Bool ?? false, holdRevisions: true)
            startLiveClearTimer()
            if translationStreams[language] == nil { translationPacers[language] = CaptionPacer() }
            let reconciliation = translationStreams[language, default: RevisableCaption()].update(
                event["text"] as? String ?? "", id: eventCaptionID, generation: translationRevision,
                final: event["final"] as? Bool ?? false
            )
            translationPacers[language]?.observe(pendingWords: translationStreams[language]?.pendingCount ?? 0)
            if let reconciliation {
                translationRetentions[language, default: CaptionRetentionBuffer()].reconcile(reconciliation)
                let tokens = preferences.effectiveCaptionFlow == .sentence
                    ? reconciliation.visibleTokens
                    : translationRetentions[language, default: CaptionRetentionBuffer()].tokens(
                        mode: preferences.effectiveCaptionFlow,
                        wordLimit: preferences.effectiveCaptionWordLimit,
                        timeLimit: preferences.effectiveCaptionTimeLimit
                    )
                translationTokens[language] = tokens
                translations[language] = tokens.map(\.text).joined().trimmingCharacters(in: .whitespacesAndNewlines)
            }
            playPendingWords()
            cachedTokens = event["cached_tokens"] as? Int ?? 0
        case "error": fail(event["message"] as? String ?? "Local inference failed.")
        default: break
        }
    }
    private func resetPlayback() {
        previewTask?.cancel(); previewTask = nil
        playbackTask?.cancel(); playbackTask = nil
        expirationTask?.cancel(); expirationTask = nil
        liveClearTask?.cancel(); liveClearTask = nil
        originalStream = ProgressiveCaption(); translationStreams = [:]; captionID = ""
        originalRetention = CaptionRetentionBuffer(); translationRetentions = [:]
        originalPacer.reset(); translationPacers = [:]
    }
    private func playPendingWords() {
        guard playbackTask == nil else { return }
        playbackTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                if let step = self.originalStream.advanceStep() {
                    self.originalRetention.merge(step)
                    let display = self.display(step: step, buffer: &self.originalRetention)
                    self.original = display.text; self.originalTokens = display.tokens
                }
                for language in Array(self.translationStreams.keys) {
                    if let step = self.translationStreams[language]?.advanceStep() {
                        self.translationRetentions[language, default: CaptionRetentionBuffer()].merge(step)
                        let display = self.display(step: step, buffer: &self.translationRetentions[language, default: CaptionRetentionBuffer()])
                        self.translations[language] = display.text; self.translationTokens[language] = display.tokens
                    }
                }
                self.startExpirationTimerIfNeeded()
                guard self.originalStream.hasPending || self.translationStreams.values.contains(where: { $0.hasPending }) else {
                    self.playbackTask = nil; return
                }
                var delays: [Double] = []
                if self.originalStream.hasPending { delays.append(self.originalPacer.milliseconds) }
                for language in self.translationStreams.keys where self.translationStreams[language]?.hasPending == true {
                    delays.append(self.translationPacers[language]?.milliseconds ?? 180)
                }
                let hasClassic = self.preferences.effectiveCaptionPresentation == .classic || self.preferences.effectiveTranslationPresentation == .classic
                if !hasClassic { continue }
                let delay = Int((delays.max() ?? 180).rounded())
                do { try await Task.sleep(for: .milliseconds(delay)) } catch { return }
            }
        }
    }
    private func display(step: CaptionStep, buffer: inout CaptionRetentionBuffer) -> (text: String, tokens: [CaptionToken]) {
        let mode = preferences.effectiveCaptionFlow
        let tokens = mode == .sentence ? step.visibleTokens : buffer.tokens(mode: mode, wordLimit: preferences.effectiveCaptionWordLimit, timeLimit: preferences.effectiveCaptionTimeLimit)
        return (tokens.map(\.text).joined().trimmingCharacters(in: .whitespacesAndNewlines), tokens)
    }
    private func applyCaptionFlowPreferences() {
        let mode = preferences.effectiveCaptionFlow
        if mode == .sentence {
            original = originalStream.text
            originalTokens = originalStream.displayTokens
            for language in translationStreams.keys {
                translations[language] = translationStreams[language]?.text ?? ""
                translationTokens[language] = translationStreams[language]?.displayTokens ?? []
            }
        } else {
            originalTokens = originalRetention.tokens(mode: mode, wordLimit: preferences.effectiveCaptionWordLimit, timeLimit: preferences.effectiveCaptionTimeLimit)
            original = originalTokens.map(\.text).joined().trimmingCharacters(in: .whitespacesAndNewlines)
            for language in translationRetentions.keys {
                let tokens = translationRetentions[language]?.tokens(mode: mode, wordLimit: preferences.effectiveCaptionWordLimit, timeLimit: preferences.effectiveCaptionTimeLimit) ?? []
                translationTokens[language] = tokens
                translations[language] = tokens.map(\.text).joined().trimmingCharacters(in: .whitespacesAndNewlines)
            }
        }
        startExpirationTimerIfNeeded()
    }
    private func startLiveClearTimer() {
        liveClearTask?.cancel(); liveClearTask = nil
        guard preferences.effectiveCaptionClearAfter > 0 else { return }
        liveClearTask = Task { [weak self] in
            while !Task.isCancelled {
                do { try await Task.sleep(for: .milliseconds(250)) } catch { return }
                guard let self else { return }
                let seconds = self.preferences.effectiveCaptionClearAfter
                var original = self.originalDocument
                original.clearIfIdle(seconds: seconds, lastSpeech: self.lastSpeech)
                if original.cleared != self.originalDocument.cleared { self.originalDocument = original }
                for language in self.translationDocuments.keys {
                    var document = self.translationDocuments[language]!
                    document.clearIfIdle(seconds: seconds, lastSpeech: self.lastSpeech)
                    if document.cleared != self.translationDocuments[language]!.cleared {
                        self.translationDocuments[language] = document
                    }
                }
                if self.originalDocument.cleared && self.translationDocuments.values.allSatisfy(\.cleared) { break }
            }
            self?.liveClearTask = nil
        }
    }
    private func startExpirationTimerIfNeeded() {
        guard preferences.effectiveCaptionPresentation == .classic || preferences.effectiveTranslationPresentation == .classic else { return }
        guard preferences.effectiveCaptionFlow == .time, expirationTask == nil,
              !originalRetention.isEmpty || translationRetentions.values.contains(where: { !$0.isEmpty }) else { return }
        expirationTask = Task { [weak self] in
            while !Task.isCancelled {
                do { try await Task.sleep(for: .milliseconds(100)) } catch { return }
                guard let self, self.preferences.effectiveCaptionFlow == .time else { break }
                let now = Date()
                self.originalTokens = self.originalRetention.tokens(mode: .time, wordLimit: self.preferences.effectiveCaptionWordLimit, timeLimit: self.preferences.effectiveCaptionTimeLimit, now: now)
                self.original = self.originalTokens.map(\.text).joined().trimmingCharacters(in: .whitespacesAndNewlines)
                for language in self.translationRetentions.keys {
                    let tokens = self.translationRetentions[language]?.tokens(mode: .time, wordLimit: self.preferences.effectiveCaptionWordLimit, timeLimit: self.preferences.effectiveCaptionTimeLimit, now: now) ?? []
                    self.translationTokens[language] = tokens
                    self.translations[language] = tokens.map(\.text).joined().trimmingCharacters(in: .whitespacesAndNewlines)
                }
                if self.originalRetention.isEmpty && self.translationRetentions.values.allSatisfy(\.isEmpty) { break }
            }
            self?.expirationTask = nil
        }
    }
    private func fail(_ message: String) {
        stop(); error = message; phase = "Needs attention"
        Task { await community.diagnostic("session_failed", error: message) }
    }
    func firstLaunchConsent() {
        if !community.hasChoice { changeNetworkConsent() }
        else if community.enabled { Task { await community.sync() } }
    }
    func changeNetworkConsent() {
        guard !installing else { return }
        let alert = NSAlert()
        if community.enabled {
            alert.messageText = "Disable online participation?"
            alert.informativeText = "This stops listening, disables downloads, uploads and update checks, and requests deletion of this installation's uploaded reports."
            alert.addButton(withTitle: "Keep enabled"); alert.addButton(withTitle: "Disable and delete")
            if alert.runModal() == .alertSecondButtonReturn {
                stop()
                Task { await community.revoke(); phase = "Local-only mode" }
            }
        } else {
            alert.messageText = "Share performance data?"
            alert.informativeText = "Yes enables model downloads, sends hardware specifications and error fingerprints to Ishan's Hetzner service, and checks for signed updates. Audio, captions and translations are never uploaded. Reports expire after 30 days. No keeps all internet access disabled; supply runtime and model files offline. You can change this choice later."
            alert.addButton(withTitle: "No, stay local"); alert.addButton(withTitle: "Yes, participate")
            community.choose(alert.runModal() == .alertSecondButtonReturn)
            phase = community.enabled ? "Online participation enabled" : "Local-only mode"
        }
    }
    func stop() {
        resetPlayback()
        startLiveClearTimer()
        session = UUID(); captureTask?.cancel(); captureTask = nil
        worker?.stop(); worker = nil
        let oldAudio = audio; audio = nil
        busy = false; listening = false; level = 0; phase = "Stopped"
        Task { await oldAudio?.stop() }
    }
    func showPreview() {
        guard !active else { return }
        resetPlayback()
        preview = true; original = ""; translations = [:]; originalTokens = []; translationTokens = [:]
        originalDocument = LiveCaptionDocument(); translationDocuments = [:]; revision = -1
        lastSpeech = .distantPast
        let sentences = [
            "Live captions should be easy to follow, even when the speaker keeps talking.",
            "New words appear promptly. Completed lines stay still until another line needs room.",
            "You can choose two, three, or four visible lines and turn gentle animation off.",
            "Phrase blocks keep related words together. The transcript lets you scroll back and read at your own pace.",
            "Scroll up to pause following, then choose Back to live when you are ready to catch up.",
            "This is sample text. No microphone, model, or network connection is used for this preview."
        ]
        let translations = ["French": "Un peu plus de compréhension, une phrase à la fois.", "Spanish": "Un poco más de comprensión, una frase a la vez.", "German": "Ein bisschen mehr Verständnis, Satz für Satz."]
        previewTask = Task { [weak self] in
            for (index, sentence) in sentences.enumerated() {
                let words = ProgressiveCaption.words(sentence)
                for count in stride(from: 1, through: words.count, by: 1) {
                    guard let self, !Task.isCancelled else { return }
                    let final = count == words.count
                    self.receive(["type": "source", "caption_id": "preview-\(index)", "revision": index,
                                  "text": words.prefix(count).joined(), "final": final])
                    if final {
                        for language in self.preferences.targets {
                            self.receive(["type": "translation", "caption_id": "preview-\(index)", "revision": index,
                                          "language": language, "text": translations[language] ?? sentence, "final": true])
                        }
                    }
                    do { try await Task.sleep(for: .milliseconds(100)) } catch { return }
                }
            }
            self?.phase = "Style preview · sample text"
            self?.previewTask = nil
        }
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
