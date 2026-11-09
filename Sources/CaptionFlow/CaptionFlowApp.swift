import SwiftUI
import AppKit

@main
struct CaptionFlowApp: App {
    @StateObject private var state = AppState()
    @StateObject private var presentation = WindowPresentation()
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    var body: some Scene {
        Window("CaptionFlow", id: "main") {
            ContentView().environmentObject(state).environmentObject(presentation)
                .onAppear { delegate.onTerminate = { state.stop() } }
        }
        .defaultSize(width: 1160, height: 740)
        .commands {
            CommandGroup(after: .toolbar) {
                Button(presentation.isFullScreen ? "Exit Full Screen" : "Enter Full Screen") { presentation.toggleFullScreen() }
                    .keyboardShortcut("f", modifiers: [.control, .command])
            }
            CommandGroup(after: .newItem) {
                Button(state.active ? "Stop Listening" : "Start Listening") { state.active ? state.stop() : state.start() }.keyboardShortcut("r")
                Button("Toggle Inspector") {
                    if presentation.isFullScreen { state.fullScreenInspector.toggle() }
                    else { state.inspector.toggle() }
                }.keyboardShortcut("i", modifiers: [.command, .option])
                Button("Preview Appearance") { state.showPreview() }.keyboardShortcut("p", modifiers: [.command, .shift]).disabled(state.active)
            }
        }
        Settings { InspectorView().environmentObject(state).frame(width: 380, height: 640) }
    }
}
final class AppDelegate: NSObject, NSApplicationDelegate {
    var onTerminate: (() -> Void)?
    func applicationDidFinishLaunching(_ notification: Notification) { NSApp.setActivationPolicy(.regular); NSApp.activate(ignoringOtherApps: true) }
    func applicationWillTerminate(_ notification: Notification) { onTerminate?() }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}
struct ContentView: View {
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var presentation: WindowPresentation
    var body: some View {
        HStack(spacing: 0) {
            SubtitleCanvas(fullScreen: presentation.isFullScreen)
            if presentation.isFullScreen ? state.fullScreenInspector : state.inspector {
                Divider()
                InspectorView().frame(width: 320)
            }
        }
        .frame(minWidth: 780, minHeight: 540)
        .background(WindowAttachment(presentation: presentation).frame(width: 0, height: 0))
        .ignoresSafeArea(edges: presentation.isFullScreen ? .all : [])
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                CommunityControls(support: state.community, changeConsent: state.changeNetworkConsent)
                    .disabled(state.installing)
                Button { presentation.toggleFullScreen() } label: { Image(systemName: "arrow.up.left.and.arrow.down.right") }
                    .help("Full screen (⌃⌘F)").accessibilityLabel("Full screen")
                Button { withAnimation(.easeInOut(duration: 0.18)) { state.inspector.toggle() } } label: { Image(systemName: "sidebar.right") }
                    .help("Inspector (⌥⌘I)").accessibilityLabel("Inspector")
                Button { state.active ? state.stop() : state.start() } label: {
                    Label(state.active ? "Stop" : "Start listening", systemImage: state.active ? "stop.fill" : "mic.fill")
                }
                .buttonStyle(.borderedProminent).disabled(state.installing).help("Start or stop (⌘R)")
            }
        }
        .toolbar(presentation.isFullScreen ? .hidden : .visible, for: .windowToolbar)
        .alert("CaptionFlow needs attention", isPresented: Binding(get: { state.error != nil }, set: { if !$0 { state.error = nil } })) {
            if state.capturePermissionIssue {
                Button("Open System Settings") { state.openCaptureSettings() }
            }
            Button("OK") { state.error = nil }
        } message: { Text(state.error ?? "") }
        .onAppear { state.scan(); state.firstLaunchConsent() }
        .onChange(of: presentation.isFullScreen) { _, fullScreen in
            if fullScreen { state.fullScreenInspector = false }
        }
    }
}
struct CommunityControls: View {
    @ObservedObject var support: CommunitySupport
    var changeConsent: () -> Void
    var body: some View {
        Button("Privacy / sharing…", action: changeConsent)
        if support.enabled, let update = support.update {
            Button("Update to \(update["version"] as? String ?? "new version")") {
                Task { await support.downloadUpdate() }
            }
        }
    }
}
struct SubtitleCanvas: View {
    @EnvironmentObject private var state: AppState
    var fullScreen = false
    private var prefs: Preferences { state.preferences }
    var body: some View {
        GeometryReader { geometry in
            ZStack {
                prefs.background.color.ignoresSafeArea()
                if !hasVisibleCaptions {
                    if !fullScreen {
                        VStack(spacing: 18) {
                            if state.busy || state.installing {
                                ProgressView().controlSize(.small)
                                Text(state.phase).font(.body).multilineTextAlignment(.center)
                            } else {
                                Text(state.listening ? "Listening…" : "Ready to listen")
                                    .font(.system(size: 28, weight: .medium))
                            }
                        }
                        .foregroundStyle(prefs.foreground.color.opacity(0.7)).padding(40)
                    }
                } else {
                    let laneCount = max(1, (prefs.displayMode != .translated ? 1 : 0) + (prefs.displayMode != .original ? prefs.targets.count : 0))
                    let laneHeight = max(prefs.fontSize * 1.5, (geometry.size.height - 64 - Double(laneCount - 1) * 32) / Double(laneCount))
                    ScrollView {
                        VStack(spacing: 32) {
                            if prefs.displayMode != .translated {
                                caption(state.originalTokens, label: "Original").frame(height: laneHeight)
                            }
                            if prefs.displayMode != .original {
                                ForEach(prefs.targets, id: \.self) { language in
                                    caption(state.translationTokens[language] ?? [], label: language).frame(height: laneHeight)
                                }
                            }
                        }
                        .padding(.horizontal, max(32, geometry.size.width * 0.065))
                        .padding(.vertical, 32)
                        .frame(maxWidth: .infinity, minHeight: geometry.size.height)
                    }
                    .scrollIndicators(.hidden)
                }
            }
            .foregroundStyle(prefs.foreground.color)
        }
    }
    private func caption(_ tokens: [CaptionToken], label: String) -> some View {
        CaptionWordsView(tokens: tokens, label: label, typeface: prefs.typeface, fontSize: prefs.fontSize)
    }
    private var hasVisibleCaptions: Bool {
        let hasOriginal = prefs.displayMode != .translated && !state.originalTokens.isEmpty
        let hasTranslation = prefs.displayMode != .original && prefs.targets.contains { !(state.translationTokens[$0] ?? []).isEmpty }
        return hasOriginal || hasTranslation
    }
}
struct InspectorView: View {
    @EnvironmentObject private var state: AppState
    @State private var customLanguage = ""
    private let fonts = ["System"] + NSFontManager.shared.availableFontFamilies.sorted()
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Picker("Inspector", selection: $state.tab) { Text("Style").tag("Style"); Text("Languages").tag("Languages"); Text("Models").tag("Models") }.pickerStyle(.segmented).labelsHidden().padding(16)
            Form {
                switch state.tab {
                case "Style": style
                case "Languages": languages
                default: models
                }
            }
            .formStyle(.grouped)
        }
        .background(.background.secondary)
    }
    private var style: some View {
        Group {
            Section("Typography") {
                Picker("Typeface", selection: $state.preferences.typeface) { ForEach(fonts, id: \.self) { Text($0).tag($0) } }
                VStack(alignment: .leading, spacing: 10) {
                    HStack { Text("Size"); Spacer(); Text("\(Int(state.preferences.fontSize)) pt").foregroundStyle(.secondary).monospacedDigit() }
                    Slider(value: $state.preferences.fontSize, in: 24...120, step: 2).accessibilityLabel("Font size")
                }
            }
            Section("Caption flow") {
                Picker("Keep on screen", selection: Binding(
                    get: { state.preferences.effectiveCaptionFlow },
                    set: { state.preferences.captionFlow = $0 }
                )) {
                    ForEach(CaptionFlowMode.allCases, id: \.self) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                if state.preferences.effectiveCaptionFlow == .words {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack { Text("Maximum"); Spacer(); Text("\(state.preferences.effectiveCaptionWordLimit) words").foregroundStyle(.secondary).monospacedDigit() }
                        Slider(value: Binding(
                            get: { Double(state.preferences.effectiveCaptionWordLimit) },
                            set: { state.preferences.captionWordLimit = Int($0) }
                        ), in: 3...60, step: 1).accessibilityLabel("Maximum words on screen")
                    }
                } else if state.preferences.effectiveCaptionFlow == .time {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack { Text("Keep each word"); Spacer(); Text(state.preferences.effectiveCaptionTimeLimit, format: .number.precision(.fractionLength(1))); Text("s").foregroundStyle(.secondary) }
                        Slider(value: Binding(
                            get: { state.preferences.effectiveCaptionTimeLimit },
                            set: { state.preferences.captionTimeLimit = $0 }
                        ), in: 1...20, step: 0.5).accessibilityLabel("Time each word remains on screen")
                    }
                }
                Text(flowDescription).font(.caption).foregroundStyle(.secondary)
            }
            Section("Canvas") {
                ColorPicker("Text", selection: Binding(get: { state.preferences.foreground.color }, set: { state.preferences.foreground = RGBA($0) }), supportsOpacity: false)
                ColorPicker("Background", selection: Binding(get: { state.preferences.background.color }, set: { state.preferences.background = RGBA($0) }), supportsOpacity: false)
                HStack {
                    preset("Midnight", fg: RGBA(0.96, 0.96, 0.94), bg: RGBA(0.065, 0.075, 0.085))
                    preset("Paper", fg: RGBA(0.14, 0.16, 0.17), bg: RGBA(0.96, 0.95, 0.91))
                    preset("Cinema", fg: RGBA(1, 0.86, 0.3), bg: RGBA(0, 0, 0))
                }
            }
            Section {
                Button("Preview", systemImage: "text.quote") { state.showPreview() }.disabled(state.active)
            }
        }
    }
    private var flowDescription: String {
        switch state.preferences.effectiveCaptionFlow {
        case .sentence: return "Replace the previous sentence when the next one begins."
        case .words: return "Keep a rolling window; the oldest word leaves as each new word appears."
        case .time: return "Each word disappears individually after its time expires."
        }
    }
    private func preset(_ title: String, fg: RGBA, bg: RGBA) -> some View {
        Button { state.preferences.foreground = fg; state.preferences.background = bg } label: {
            Text("Aa").font(.system(size: 20, weight: .medium)).foregroundStyle(fg.color).frame(maxWidth: .infinity).padding(.vertical, 9).background(bg.color, in: RoundedRectangle(cornerRadius: 6)).overlay(RoundedRectangle(cornerRadius: 6).stroke(.gray.opacity(0.25)))
        }.buttonStyle(.plain).help(title).accessibilityLabel("\(title) colors")
    }
    private var languages: some View {
        Group {
            Section("Listening") {
                Picker("Audio", selection: $state.preferences.source) { ForEach(AudioSource.allCases, id: \.self) { Text($0.rawValue).tag($0) } }
                Picker("Spoken language", selection: $state.preferences.sourceLanguage) {
                    Text("Detect automatically").tag("auto")
                    ForEach(["en", "fr", "es", "de", "it", "pt", "nl", "pl", "ru", "uk", "ar", "hi", "zh", "ja", "ko"], id: \.self) { code in Text(Locale.current.localizedString(forLanguageCode: code) ?? code).tag(code) }
                }
                Text(state.preferences.source == .microphone ? "Uses your Mac’s current input device." : "Captures audio playing on your Mac. macOS may request Screen & System Audio Recording access.").font(.caption).foregroundStyle(.secondary)
            }.disabled(state.active)
            Section("On screen") {
                Picker("Show", selection: $state.preferences.displayMode) { ForEach(DisplayMode.allCases, id: \.self) { Text($0.rawValue).tag($0) } }
            }.disabled(state.active)
            Section("Translate into") {
                ForEach(Array(Set(Preferences.languages + state.preferences.targets)).sorted(), id: \.self) { language in
                    Toggle(language, isOn: Binding(get: { state.preferences.targets.contains(language) }, set: { enabled in
                        if enabled { state.preferences.targets.append(language) } else { state.preferences.targets.removeAll { $0 == language } }
                    })).toggleStyle(.checkbox)
                }
                HStack {
                    TextField("Another language", text: $customLanguage)
                    Button { let value = customLanguage.trimmingCharacters(in: .whitespacesAndNewlines); if !value.isEmpty && !state.preferences.targets.contains(value) { state.preferences.targets.append(value) }; customLanguage = "" } label: { Image(systemName: "plus") }.accessibilityLabel("Add translation language")
                }
                Text("Each language streams separately. More languages increase delay. Stop listening to change language or model settings.").font(.caption).foregroundStyle(.secondary)
            }.disabled(state.active)
        }
    }
    private var models: some View {
        Group {
            Section("Local inference") {
                Label(state.installed ? "MLX runtime installed" : "One-time setup", systemImage: state.installed ? "checkmark.circle.fill" : "shippingbox").foregroundStyle(state.installed ? Color.green : .primary)
                if state.busy { ProgressView().controlSize(.small); Text(state.phase).font(.caption).foregroundStyle(.secondary) }
                if !state.installed {
                    Text("Install a private MLX runtime. Internet is needed for setup and optional model downloads; audio and captions remain on your Mac.").font(.caption).foregroundStyle(.secondary)
                    TextField("Python 3.11+ path", text: $state.preferences.pythonPath).font(.caption)
                    Button(state.installing ? "Installing…" : "Install local runtime") { state.install() }.disabled(state.installing)
                    if state.installing { ProgressView().controlSize(.small); Text(state.phase).font(.caption).lineLimit(4) }
                }
            }
            Section("Model folders · optional") {
                Text("Add any number of roots. Existing MLX models are used in place, including Hugging Face snapshots.").font(.caption).foregroundStyle(.secondary)
                ForEach(state.preferences.roots, id: \.self) { path in
                    HStack {
                        Image(systemName: "folder").foregroundStyle(.secondary)
                        VStack(alignment: .leading) { Text(URL(fileURLWithPath: path).lastPathComponent); Text(path).font(.caption2).foregroundStyle(.secondary).lineLimit(2).help(path) }
                        Spacer()
                        Button { state.preferences.roots.removeAll { $0 == path }; state.scan() } label: { Image(systemName: "minus.circle") }.buttonStyle(.plain).accessibilityLabel("Remove \(path)")
                    }
                }
                HStack {
                    Button("Add folders…", systemImage: "folder.badge.plus") { state.addRoots() }
                    Spacer()
                    Button { state.scan() } label: { Image(systemName: "arrow.clockwise") }.disabled(state.scanning).help("Rescan folders")
                }
                .dropDestination(for: URL.self) { urls, _ in state.addRoots(urls); return true }
                Text(state.scanning ? "Looking for models…" : state.scanMessage).font(.caption).foregroundStyle(.secondary)
            }.disabled(state.active || state.installing)
            Section("Models") {
                speechModelPicker
                Text(state.speechModelNote).font(.caption).foregroundStyle(.secondary)
                if state.usesVoxtral { voxtralSettings }
                translationModelPicker
                Text(state.translationModelNote).font(.caption).foregroundStyle(.secondary)
                Toggle("Download missing models", isOn: $state.preferences.allowDownloads)
                Text("Leave folders empty to use app-managed copies. Disable downloads for strictly offline startup. Models must be MLX-compatible; GGUF and PyTorch-only weights are not interchangeable.").font(.caption).foregroundStyle(.secondary)
            }.disabled(state.active || state.installing)
            DisclosureGroup("Audio sensitivity") {
                Slider(value: $state.preferences.silenceThreshold, in: 0.001...0.04).accessibilityLabel("Silence threshold")
                Text("Lower this for quiet voices; raise it to ignore background noise.").font(.caption).foregroundStyle(.secondary)
            }.disabled(state.active)
        }
    }
    private var voxtralSettings: some View {
        Group {
            Toggle("Fast draft + corrections (experimental)", isOn: Binding(
                get: { state.preferences.effectiveVoxtralDualDelay },
                set: { state.preferences.voxtralDualDelay = $0 }
            ))
            Picker(state.preferences.effectiveVoxtralDualDelay ? "Draft delay" : "Recognition delay", selection: Binding(
                get: { state.preferences.effectiveVoxtralDelayMS },
                set: { state.preferences.voxtralDelayMS = $0 }
            )) {
                ForEach(Preferences.voxtralDelays.filter { !state.preferences.effectiveVoxtralDualDelay || $0 < 2400 }, id: \.self) {
                    Text("\($0) ms").tag($0)
                }
            }
            if state.preferences.effectiveVoxtralDualDelay {
                Picker("Correction delay", selection: Binding(
                    get: { state.preferences.effectiveVoxtralCorrectionDelayMS },
                    set: { state.preferences.voxtralCorrectionDelayMS = $0 }
                )) {
                    ForEach(Preferences.voxtralDelays.filter { $0 > state.preferences.effectiveVoxtralDelayMS }, id: \.self) {
                        Text("\($0) ms").tag($0)
                    }
                }
            }
            Text(state.preferences.effectiveVoxtralDualDelay
                 ? "Shows fast words, then corrects them with more audio context. Runs two local sessions and uses more compute and memory. Translation follows the revised text."
                 : "Shorter delays respond sooner; longer delays give recognition more audio context. These are model delays; processing adds latency.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
    private var speechModelPicker: some View {
        Picker("Speech", selection: $state.preferences.whisperModel) {
            Text("Whisper Small").tag("mlx-community/whisper-small-mlx")
            Text("Parakeet TDT 0.6B v3").tag("mlx-community/parakeet-tdt-0.6b-v3")
            Text("Qwen3-ASR · 1.7B · 4-bit").tag(Preferences.qwen3ASRSpeechModel)
            Text("Voxtral Realtime 4B · 4-bit").tag("mlx-community/Voxtral-Mini-4B-Realtime-2602-4bit")
            ForEach(state.models.filter { $0.kind == .speech }) { Text("Local · \($0.name)").tag($0.path) }
            let selection = state.preferences.whisperModel
            if !["mlx-community/whisper-small-mlx", "mlx-community/parakeet-tdt-0.6b-v3", Preferences.qwen3ASRSpeechModel, "mlx-community/Voxtral-Mini-4B-Realtime-2602-4bit"].contains(selection),
               !state.models.contains(where: { $0.path == selection }) {
                Text(URL(fileURLWithPath: selection).lastPathComponent + " (saved)").tag(selection)
            }
        }
    }
    private var translationModelPicker: some View {
        Picker("Translation", selection: $state.preferences.translationModel) {
            Text("Hy-MT2 · 1.8B · 4-bit · translation").tag(Preferences.hyMT2TranslationModel)
            Text("Qwen 3.5 · 2B · 4-bit").tag(Preferences.qwen35TwoBTranslationModel)
            Text("Qwen 3.5 · 0.8B · 4-bit · lighter").tag(Preferences.qwen35TranslationModel)
            Text("Qwen 2.5 · 1.5B · 4-bit").tag(Preferences.qwen25TranslationModel)
            ForEach(state.models.filter { $0.kind == .translation }) { Text("Local · \($0.name)").tag($0.path) }
            let selection = state.preferences.translationModel
            if ![Preferences.hyMT2TranslationModel, Preferences.qwen35TwoBTranslationModel, Preferences.qwen35TranslationModel, Preferences.qwen25TranslationModel].contains(selection),
               !state.models.contains(where: { $0.path == selection }) {
                Text(URL(fileURLWithPath: selection).lastPathComponent + " (saved)").tag(selection)
            }
        }
    }
    private func modelPicker(_ title: String, kind: LocalModel.Kind, selection: Binding<String>, fallback: String, label: String) -> some View {
        Picker(title, selection: selection) {
            Text(label).tag(fallback)
            ForEach(state.models.filter { $0.kind == kind }) { Text("Local · \($0.name)").tag($0.path) }
            if selection.wrappedValue != fallback && !state.models.contains(where: { $0.path == selection.wrappedValue }) { Text(URL(fileURLWithPath: selection.wrappedValue).lastPathComponent + " (saved)").tag(selection.wrappedValue) }
        }
    }
}
