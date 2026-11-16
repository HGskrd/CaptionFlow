import AppKit
import Foundation
import Combine
import ObjectiveC

// Both frontends use AppState, AudioCapture, WorkerBridge and the same worker resources.
// These C entry points are called exclusively on GPUI's AppKit main thread.
@MainActor private var engine: GPUIEngine?

@MainActor
final class GPUIEngine: NSObject {
    let state: AppState
    private let canvas = CaptionCanvas()
    private var colorKey = "foreground"
    private var quitting = false
    private var stopped = false
    var dirty = true
    private var observers = Set<AnyCancellable>()
    private let fonts = ["System"] + NSFontManager.shared.availableFontFamilies.sorted()
    private var terminationSignal: DispatchSourceSignal?
    private var lastDiagnostic = Date.distantPast
    var lastTimedSnapshot = Date.distantPast
    private var loadingSince = Date()

    override init() {
        if UserDefaults.standard.data(forKey: "preferences") == nil,
           let saved = UserDefaults.standard.persistentDomain(forName: "dev.captionflow.app")?["preferences"] as? Data {
            UserDefaults.standard.set(saved, forKey: "preferences")
        }
        // Earlier GPUI builds dropped display fields they did not know about.
        // Restore only missing fields from the installed app; preserve GPUI edits.
        if let current = UserDefaults.standard.data(forKey: "preferences"),
           let previous = UserDefaults.standard.persistentDomain(forName: "dev.captionflow.app")?["preferences"] as? Data,
           var value = try? JSONSerialization.jsonObject(with: current) as? [String: Any],
           let saved = try? JSONSerialization.jsonObject(with: previous) as? [String: Any] {
            for key in ["captionPresentation", "translationPresentation", "captionVisibleLines", "captionClearAfter", "captionGentleAnimation"] where value[key] == nil {
                value[key] = saved[key]
            }
            if let data = try? JSONSerialization.data(withJSONObject: value) { UserDefaults.standard.set(data, forKey: "preferences") }
        }
        state = AppState()
        super.init()
        state.objectWillChange.sink { [weak self] in self?.dirty = true }.store(in: &observers)
        state.community.objectWillChange.sink { [weak self] in self?.dirty = true }.store(in: &observers)
        // AppKit's Dock Quit must drain the same worker as the menu and close button.
        // GPUI 0.2.2's final quit observers have only 100 ms, so defer termination here.
        if let delegate = NSApp.delegate, let delegateClass = object_getClass(delegate) {
            let block: @convention(block) (AnyObject, NSApplication) -> UInt = { _, application in
                if engine?.state.active != true { return NSApplication.TerminateReply.terminateNow.rawValue }
                Task { @MainActor in
                    await engine?.state.shutdown()
                    application.reply(toApplicationShouldTerminate: true)
                }
                return NSApplication.TerminateReply.terminateLater.rawValue
            }
            class_addMethod(delegateClass, #selector(NSApplicationDelegate.applicationShouldTerminate(_:)), imp_implementationWithBlock(block), "Q@:@")
        }
        signal(SIGTERM, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        source.setEventHandler { [weak self] in
            if self?.state.active != true { NSApp.terminate(nil); return }
            Task { @MainActor in await self?.state.shutdown(); NSApp.terminate(nil) }
        }
        source.resume(); terminationSignal = source
        state.scan()
    }

    func command(_ command: [String: Any]) {
        let type = command["type"] as? String ?? ""
        switch type {
        case "start":
            loadingSince = Date()
            if !state.community.hasChoice {
                state.changeNetworkConsent(in: mainWindow) { [weak self] in self?.state.start() }
            } else { state.start() }
        case "stop": state.stop()
        case "preview": state.showPreview()
        case "install": loadingSince = Date(); state.install()
        case "scan": state.scan()
        case "enableDiarization": if !state.active { state.preferences.diarizationModel = "nvidia/Nemotron-3-Diarization" }
        case "clearDiarization": if !state.active { state.preferences.diarizationModel = nil }
        case "addRoots": if !state.active && !state.installing { addRoots() }
        case "dropRoots": if !state.active && !state.installing { state.addRoots((command["paths"] as? [String] ?? []).map { URL(fileURLWithPath: $0) }) }
        case "removeRoot":
            guard !state.active, !state.installing else { return }
            state.preferences.roots.removeAll { $0 == command["path"] as? String }; state.scan()
        case "viewport":
            canvas.resize(width: command["width"] as? Double ?? 720, height: command["height"] as? Double ?? 280); dirty = true
        case "scroll":
            canvas.scroll(lane: command["lane"] as? String ?? "original", offset: command["offset"] as? Double ?? 0); dirty = true
        case "backToLive": canvas.backToLive(lane: command["lane"] as? String); dirty = true
        case "set": set(command["key"] as? String ?? "", command["value"] ?? NSNull())
        case "edit":
            if command["key"] as? String == "diarizationModel" {
                let panel = NSOpenPanel(); panel.canChooseFiles = true; panel.canChooseDirectories = false
                panel.message = "Choose a local Sortformer GGUF speaker detection model."
                if let window = mainWindow { panel.beginSheetModal(for: window) { [weak self] result in if result == .OK { self?.state.preferences.diarizationModel = panel.url?.path } } }
            } else { edit(command["key"] as? String ?? "") }
        case "color":
            colorKey = command["key"] as? String == "background" ? "background" : "foreground"
            let color = colorKey == "background" ? state.preferences.background : state.preferences.foreground
            let panel = NSColorPanel.shared
            panel.color = NSColor(srgbRed: color.r, green: color.g, blue: color.b, alpha: color.a)
            panel.showsAlpha = false; panel.setTarget(self); panel.setAction(#selector(colorChanged(_:)))
            panel.orderFront(nil)
        case "preset":
            switch command["value"] as? String {
            case "Paper": state.preferences.foreground = RGBA(0.14, 0.16, 0.17); state.preferences.background = RGBA(0.96, 0.95, 0.91)
            case "Cinema": state.preferences.foreground = RGBA(1, 0.86, 0.3); state.preferences.background = RGBA(0, 0, 0)
            default: state.preferences.foreground = RGBA(0.96, 0.96, 0.94); state.preferences.background = RGBA(0.065, 0.075, 0.085)
            }
        case "privacy": state.changeNetworkConsent(in: mainWindow)
        case "update": Task { await state.community.downloadUpdate() }
        case "captureSettings": state.openCaptureSettings()
        case "dismissError": state.error = nil
        case "dismissNotice": state.community.status = ""
        case "quit":
            if !state.active { NSApp.terminate(nil) }
            else { Task { await state.shutdown(); NSApp.terminate(nil) } }
        case "shutdown":
            guard !quitting else { return }
            quitting = true
            if !state.active { stopped = true; dirty = true; return }
            Task { await state.shutdown(); stopped = true; dirty = true }
        default: break
        }
    }

    @objc private func colorChanged(_ sender: NSColorPanel) {
        let color = RGBA(ColorBridge.color(sender.color))
        if colorKey == "background" { state.preferences.background = color }
        else { state.preferences.foreground = color }
    }

    private func set(_ key: String, _ value: Any) {
        let styleKeys: Set<String> = ["typeface", "fontSize", "foreground", "background", "captionFlow", "captionWordLimit", "captionTimeLimit", "captionPresentation", "translationPresentation", "captionVisibleLines", "captionClearAfter", "captionGentleAnimation"]
        guard styleKeys.contains(key) || (!state.active && !state.installing) else { return }
        guard var object = try? JSONSerialization.jsonObject(with: JSONEncoder().encode(state.preferences)) as? [String: Any],
              object.keys.contains(key) || ["captionFlow", "captionWordLimit", "captionTimeLimit", "captionPresentation", "translationPresentation", "captionVisibleLines", "captionClearAfter", "captionGentleAnimation", "voxtralDualDelay", "voxtralDelayMS", "voxtralCorrectionDelayMS"].contains(key) else { return }
        object[key] = value
        guard let encoded = try? JSONSerialization.data(withJSONObject: object),
              var preferences = try? JSONDecoder().decode(Preferences.self, from: encoded) else { return }
        preferences.fontSize = min(120, max(24, preferences.fontSize))
        preferences.silenceThreshold = min(0.04, max(0.001, preferences.silenceThreshold))
        preferences.captionWordLimit = preferences.effectiveCaptionWordLimit
        preferences.captionTimeLimit = preferences.effectiveCaptionTimeLimit
        if !Preferences.voxtralDelays.contains(preferences.effectiveVoxtralDelayMS) { return }
        state.preferences = preferences
    }

    private var mainWindow: NSWindow? { NSApp.windows.first { $0.isVisible && !($0 is NSPanel) } }

    private func addRoots() {
        guard let window = mainWindow else { return }
        let panel = NSOpenPanel()
        panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.allowsMultipleSelection = true
        panel.message = "Choose model folders or cache roots. Existing models are used in place."
        panel.beginSheetModal(for: window) { [weak self] response in
            if response == .OK { self?.state.addRoots(panel.urls) }
            DispatchQueue.main.async { window.makeFirstResponder(window.contentView) }
        }
    }

    private func edit(_ key: String) {
        guard !state.active, !state.installing else { return }
        let alert = NSAlert()
        alert.messageText = key == "language" ? "Add a translation language" : "Python 3.11+ path"
        alert.addButton(withTitle: "Save"); alert.addButton(withTitle: "Cancel")
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 340, height: 26))
        field.stringValue = key == "pythonPath" ? state.preferences.pythonPath : ""
        alert.accessoryView = field; alert.window.initialFirstResponder = field
        guard let window = mainWindow else { return }
        alert.beginSheetModal(for: window) { [weak self] response in
            defer { DispatchQueue.main.async { window.makeFirstResponder(window.contentView) } }
            guard let self, response == .alertFirstButtonReturn else { return }
            let value = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !value.isEmpty else { return }
            if key == "language" {
                if !state.preferences.targets.contains(value) { state.preferences.targets.append(value) }
            } else if key == "pythonPath" { state.preferences.pythonPath = value }
        }
    }

    private func field(_ kind: String, _ key: String, _ label: String, _ value: Any,
                       options: [[String: Any]] = [], disabled: Bool = false,
                       minimum: Double = 0, maximum: Double = 0, step: Double = 1) -> [String: Any] {
        ["kind": kind, "key": key, "label": label, "value": value, "options": options,
         "disabled": disabled, "min": minimum, "max": maximum, "step": step]
    }
    private func option(_ value: Any, _ label: String) -> [String: Any] { ["value": value, "label": label] }
    private func choices(_ values: [String]) -> [[String: Any]] { values.map { option($0, $0) } }
    private func section(_ title: String, _ fields: [[String: Any]], note: String = "") -> [String: Any] {
        ["title": title, "fields": fields, "note": note]
    }

    func snapshot() -> [String: Any] {
        let p = state.preferences
        let locked = state.active || state.installing
        var style = [
            section("Typography", [
                field("select", "typeface", "Typeface", p.typeface, options: choices(fonts)),
                field("number", "fontSize", "Size", p.fontSize, minimum: 24, maximum: 120, step: 2)
            ]),
            section("Caption flow", [
                field("select", "captionPresentation", "Original", p.effectiveCaptionPresentation.rawValue,
                      options: choices(CaptionPresentation.allCases.map(\.rawValue))),
                field("select", "translationPresentation", "Translation", p.effectiveTranslationPresentation.rawValue,
                      options: choices(CaptionPresentation.allCases.map(\.rawValue))),
                field("select", "captionVisibleLines", "Visible lines", p.effectiveCaptionVisibleLines,
                      options: (2...4).map { option($0, "\($0)") }),
                field("select", "captionClearAfter", "Clear after silence", p.effectiveCaptionClearAfter,
                      options: [option(0, "Never"), option(5, "5 s"), option(10, "10 s")]),
                field("toggle", "captionGentleAnimation", "Gentle animation", p.effectiveCaptionGentleAnimation)
            ], note: "Rolling lines stay anchored. Phrase blocks update in groups. Transcript keeps recent text in memory; scroll up to pause following. Animation respects Reduce Motion."),
            section("Colors", [field("color", "foreground", "Text", ""), field("color", "background", "Background", ""),
                field("presets", "presets", "Presets", "")]),
            section("Appearance", [field("action", "preview", "Preview appearance", "", disabled: state.active)])
        ]
        if p.effectiveCaptionPresentation == .classic || p.effectiveTranslationPresentation == .classic {
            var fields = [field("select", "captionFlow", "Keep on screen", p.effectiveCaptionFlow.rawValue,
                                options: choices(CaptionFlowMode.allCases.map(\.rawValue)))]
            if p.effectiveCaptionFlow == .words { fields.append(field("number", "captionWordLimit", "Maximum words", p.effectiveCaptionWordLimit, minimum: 3, maximum: 60)) }
            if p.effectiveCaptionFlow == .time { fields.append(field("number", "captionTimeLimit", "Keep each word (s)", p.effectiveCaptionTimeLimit, minimum: 1, maximum: 20, step: 0.5)) }
            style.insert(section("Classic retention", fields), at: 2)
        }
        let languageOptions = [option("auto", "Detect automatically")] + ["en", "fr", "es", "de", "it", "pt", "nl", "pl", "ru", "uk", "ar", "hi", "zh", "ja", "ko"].map { option($0, Locale.current.localizedString(forLanguageCode: $0) ?? $0) }
        let languages = [
            section("Listening", [
                field("select", "source", "Audio", p.source.rawValue, options: choices(AudioSource.allCases.map(\.rawValue)), disabled: locked),
                field("select", "sourceLanguage", "Spoken language", p.sourceLanguage, options: languageOptions, disabled: locked)
            ], note: p.source == .microphone ? "Uses your Mac’s current input device." : "Captures audio playing on your Mac."),
            section("On screen", [field("select", "displayMode", "Show", p.displayMode.rawValue, options: choices(DisplayMode.allCases.map(\.rawValue)), disabled: locked)]),
            section("Translate into", Array(Set(Preferences.languages + p.targets)).sorted().map {
                field("target", $0, $0, p.targets.contains($0), disabled: locked)
            } + [field("edit", "language", "Add another language…", "", disabled: locked)],
            note: "Each language streams separately. More languages increase delay. Stop listening to change language or model settings.")
        ]
        var speechOptions = [option(Preferences.whisperSmallSpeechModel, "Whisper Small"), option(Preferences.parakeetSpeechModel, "Parakeet TDT 0.6B v3"), option(Preferences.qwen3ASRSpeechModel, "Qwen3-ASR 1.7B · 4-bit"), option(Preferences.voxtralSpeechModel, "Voxtral Realtime 4B · 4-bit")]
        if !Preferences.supportsMLX { speechOptions = [option(p.effectiveSpeechModel, "Nemotron · streaming · CPU")] }
        speechOptions += state.models.filter { $0.kind == .speech }.map { option($0.path, "Local · " + $0.name) }
        if !speechOptions.contains(where: { $0["value"] as? String == p.speechModel }) { speechOptions.append(option(p.speechModel, URL(fileURLWithPath: p.speechModel).lastPathComponent + " (saved)")) }
        var translationOptions = [option(Preferences.hyMT2TranslationModel, "Hy-MT2 1.8B · 4-bit"), option(Preferences.qwen35TwoBTranslationModel, "Qwen 3.5 2B · 4-bit"), option(Preferences.qwen35TranslationModel, "Qwen 3.5 0.8B · 4-bit"), option(Preferences.qwen25TranslationModel, "Qwen 2.5 1.5B · 4-bit")]
        if !Preferences.supportsMLX { translationOptions = [option(p.effectiveTranslationModel, "Hy-MT2 · GGUF · CPU")] }
        translationOptions += state.models.filter { $0.kind == .translation }.map { option($0.path, "Local · " + $0.name) }
        if !translationOptions.contains(where: { $0["value"] as? String == p.translationModel }) { translationOptions.append(option(p.translationModel, URL(fileURLWithPath: p.translationModel).lastPathComponent + " (saved)")) }
        var modelFields = [field("select", "whisperModel", "Speech", p.effectiveSpeechModel, options: speechOptions, disabled: locked || !Preferences.supportsMLX)]
        if state.usesVoxtral {
            modelFields += [field("toggle", "voxtralDualDelay", "Fast draft + corrections", p.effectiveVoxtralDualDelay, disabled: locked),
                field("select", "voxtralDelayMS", p.effectiveVoxtralDualDelay ? "Draft delay" : "Recognition delay", p.effectiveVoxtralDelayMS, options: Preferences.voxtralDelays.filter { !p.effectiveVoxtralDualDelay || $0 < 2400 }.map { option($0, "\($0) ms") }, disabled: locked)]
            if p.effectiveVoxtralDualDelay { modelFields.append(field("select", "voxtralCorrectionDelayMS", "Correction delay", p.effectiveVoxtralCorrectionDelayMS, options: Preferences.voxtralDelays.filter { $0 > p.effectiveVoxtralDelayMS }.map { option($0, "\($0) ms") }, disabled: locked)) }
        }
        modelFields += [field("select", "translationModel", "Translation", p.effectiveTranslationModel, options: translationOptions, disabled: locked || !Preferences.supportsMLX),
            field("toggle", "allowDownloads", "Download missing models", p.allowDownloads && state.community.enabled, disabled: locked || !state.community.enabled)]
        modelFields += [field("action", "enableDiarization", "Enable speaker detection (~107 MB)", "", disabled: locked || p.effectiveSpeechModel != Preferences.nemotronSpeechModel || p.diarizationModel != nil),
            field("edit", "diarizationModel", "Speaker detection model…", p.diarizationModel.map { URL(fileURLWithPath: $0).lastPathComponent } ?? "None", disabled: locked || p.effectiveSpeechModel != Preferences.nemotronSpeechModel),
            field("action", "clearDiarization", "Disable speaker detection", "", disabled: locked || p.diarizationModel == nil)]
        let models = [
            section("Local inference", [field("status", "installed", state.installed ? "Local runtime installed" : "Runtime setup required", "")]
                + (state.installed ? [] : [field("edit", "pythonPath", "Python 3.11+ path", p.pythonPath, disabled: locked), field("action", "install", state.installing ? "Installing…" : "Install local runtime", "", disabled: locked)])),
            section("Model folders", p.roots.map { field("root", $0, URL(fileURLWithPath: $0).lastPathComponent, $0, disabled: locked) }
                + [field("action", "addRoots", "Add folders…", "", disabled: locked), field("action", "scan", state.scanning ? "Scanning…" : "Rescan folders", "", disabled: locked || state.scanning)], note: state.scanMessage),
            section("Models", modelFields, note: state.speechModelNote + "\n" + state.translationModelNote + (state.community.enabled ? "" : "\nLocal-only mode disables downloads. Change this under Privacy / sharing.")),
            section("Audio sensitivity", [field("number", "silenceThreshold", "Silence threshold", p.silenceThreshold, disabled: locked, minimum: 0.001, maximum: 0.04, step: 0.001)], note: "Lower this for quiet voices; raise it to ignore background noise.")
        ]
        let preferences = (try? JSONSerialization.jsonObject(with: JSONEncoder().encode(p))) ?? [:]
        let canvasState = canvas.snapshot(state: state)
        let original = (p.effectiveCaptionPresentation == .classic ? state.originalTokens : state.originalDocument.visibleTokens(for: p.effectiveCaptionPresentation)).map(\.text).joined()
        var translations: [String: String] = [:]
        for language in p.targets {
            let tokens = p.effectiveTranslationPresentation == .classic ? state.translationTokens[language] ?? []
                : state.translationDocuments[language]?.visibleTokens(for: p.effectiveTranslationPresentation) ?? []
            translations[language] = tokens.map(\.text).joined()
        }
        let input = p.source.rawValue
        let audioState = state.capturedSeconds == 0 ? "Waiting for \(input.lowercased())…"
            : state.audioLevel < 0.0001 ? "Waiting for speech from \(input.lowercased())…" : "Receiving \(input.lowercased())…"
        if Date().timeIntervalSince(lastDiagnostic) >= 1 {
            lastDiagnostic = Date()
            // Operational counts only: never persist audio or caption content.
            let diagnostic: [String: Any] = ["pid": ProcessInfo.processInfo.processIdentifier,
                "phase": state.phase, "source": p.source.rawValue, "listening": state.listening,
                "stopping": state.stopping, "capturedSeconds": state.capturedSeconds,
                "audioRMS": state.audioLevel, "sourceEvents": state.sourceEventCount,
                "translationEvents": state.translationEventCount,
                "originalCharacters": original.count,
                "translationCharacters": translations.mapValues(\.count)]
            let directory = WorkerBridge.support.appendingPathComponent("GPUI/diagnostics")
            try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            if let data = try? JSONSerialization.data(withJSONObject: diagnostic, options: [.sortedKeys]) {
                try? data.write(to: directory.appendingPathComponent("current.json"), options: .atomic)
            }
        }
        let phase = state.phase + ((state.busy || state.installing) ? " · \(Int(Date().timeIntervalSince(loadingSince)))s elapsed" : "")
        return ["preferences": preferences, "sections": [style, languages, models],
                "original": original, "translations": translations, "canvas": canvasState,
                "phase": phase, "active": state.active, "listening": state.listening,
                "audioState": audioState, "capturedSeconds": state.capturedSeconds,
                "audioRMS": state.audioLevel, "sourceEvents": state.sourceEventCount,
                "inputStatus": state.listening ? String(format: "%@ · %.1f s received", input, state.capturedSeconds) : phase,
                "busy": state.busy, "stopping": state.stopping, "installing": state.installing,
                "installed": state.installed, "error": state.error ?? "", "capturePermissionIssue": state.capturePermissionIssue,
                "online": state.community.enabled, "notice": state.community.status, "update": state.community.update?["version"] as? String ?? "",
                "shutdownComplete": stopped]
    }
}

import SwiftUI
private enum ColorBridge { static func color(_ value: NSColor) -> Color { Color(nsColor: value) } }

@_cdecl("captionflow_initialize") @MainActor
public func captionflowInitialize() { if engine == nil { engine = GPUIEngine() } }

@_cdecl("captionflow_command") @MainActor
public func captionflowCommand(_ json: UnsafePointer<CChar>) {
    guard let data = String(cString: json).data(using: .utf8),
          let command = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
    // Native panels run a nested AppKit loop. Dispatch after the GPUI callback
    // returns so that loop never re-enters an already borrowed GPUI context.
    if ["viewport", "scroll", "backToLive"].contains(command["type"] as? String ?? "") {
        // Pure presentation changes do not enter AppKit's event loop. Apply them
        // before GPUI's next frame to keep paused transcript panes aligned.
        engine?.command(command)
    } else { DispatchQueue.main.async { engine?.command(command) } }
}

@_cdecl("captionflow_snapshot") @MainActor
public func captionflowSnapshot() -> UnsafeMutablePointer<CChar>? {
    guard let engine else { return nil }
    if (engine.state.busy || engine.state.installing), Date().timeIntervalSince(engine.lastTimedSnapshot) >= 1 {
        engine.lastTimedSnapshot = Date(); engine.dirty = true
    }
    guard engine.dirty, let data = try? JSONSerialization.data(withJSONObject: engine.snapshot()),
          let text = String(data: data, encoding: .utf8) else { return nil }
    engine.dirty = false
    return strdup(text)
}

@_cdecl("captionflow_free")
public func captionflowFree(_ pointer: UnsafeMutablePointer<CChar>) { free(pointer) }
