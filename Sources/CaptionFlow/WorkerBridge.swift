import Foundation
import Darwin

/// A private JSON-lines subprocess. No HTTP server or cloud inference.
final class WorkerBridge: @unchecked Sendable {
    private var process: Process?
    private let input = Pipe()
    private let queue = DispatchQueue(label: "captionflow.worker.write")
    private let lock = NSLock()
    private var pendingAudio: Data? // One bounded slot; never accumulate stale audio on a blocked pipe.
    private var writingAudio = false
    var onEvent: (@Sendable ([String: Any]) -> Void)?
    var onExit: (@Sendable (String) -> Void)?
    init() { signal(SIGPIPE, SIG_IGN) }
    static var support: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("CaptionFlow")
    }
    static var python: URL { support.appendingPathComponent("runtime/bin/python3") }
    static var resources: URL {
        Bundle.main.url(forResource: "worker", withExtension: nil) ?? Bundle.module.url(forResource: "worker", withExtension: nil)!
    }
    static var isInstalled: Bool { FileManager.default.fileExists(atPath: support.appendingPathComponent("runtime/.ready-v3").path) }

    static func install(using pythonPath: String, progress: @escaping @Sendable (String) -> Void) async throws {
        try await Task.detached {
            try FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
            func run(_ executable: String, _ arguments: [String]) throws {
                let p = Process(); p.executableURL = URL(fileURLWithPath: executable); p.arguments = arguments
                let log = Pipe(); p.standardOutput = log; p.standardError = log
                try p.run()
                var tail = ""
                while true {
                    let data = log.fileHandleForReading.availableData
                    if data.isEmpty { break }
                    let text = String(decoding: data, as: UTF8.self)
                    tail = String((tail + text).suffix(3000))
                    progress(String(text.trimmingCharacters(in: .whitespacesAndNewlines).suffix(250)))
                }
                p.waitUntilExit()
                if p.terminationStatus != 0 { throw NSError(domain: "Runtime setup", code: Int(p.terminationStatus), userInfo: [NSLocalizedDescriptionKey: tail]) }
            }
            progress("Creating a private Python environment…")
            try run(pythonPath, ["-m", "venv", support.appendingPathComponent("runtime").path])
            progress("Installing MLX speech and translation dependencies…")
            try run(python.path, ["-m", "pip", "install", "--disable-pip-version-check", "-r", resources.appendingPathComponent("requirements.txt").path])
            try Data().write(to: support.appendingPathComponent("runtime/.ready-v3"))
        }.value
    }
    func start(configuration: [String: Any]) throws {
        let p = Process(); p.executableURL = Self.python
        p.arguments = ["-u", Self.resources.appendingPathComponent("worker.py").path]
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"; environment["TOKENIZERS_PARALLELISM"] = "false"
        environment["PYTHONDONTWRITEBYTECODE"] = "1" // Keep the signed resource directory immutable.
        environment["HF_HUB_DISABLE_TELEMETRY"] = "1"
        // The standard HTTPS path is more reliable on filtered networks and proxies.
        environment["HF_HUB_DISABLE_XET"] = "1"
        p.environment = environment
        let output = Pipe(); let errors = Pipe()
        p.standardInput = input; p.standardOutput = output; p.standardError = errors
        // Consume stderr so download progress cannot fill the pipe. It is not persisted.
        errors.fileHandleForReading.readabilityHandler = { handle in _ = handle.availableData }
        try p.run(); process = p
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var buffer = Data()
            while true {
                let data = output.fileHandleForReading.availableData
                if data.isEmpty { break }
                buffer.append(data)
                while let end = buffer.firstIndex(of: 10) {
                    let line = buffer[..<end]; buffer.removeSubrange(...end)
                    if let value = try? JSONSerialization.jsonObject(with: line) as? [String: Any] { self?.onEvent?(value) }
                }
            }
            // Drain protocol errors before reporting a generic process exit.
            p.waitUntilExit()
            errors.fileHandleForReading.readabilityHandler = nil
            self?.onExit?("Inference worker exited (\(p.terminationStatus)).")
        }
        send(configuration)
    }
    func send(_ object: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: object) else { return }
        queue.async { [weak self] in self?.write(data + Data([10])) }
    }
    func sendAudio(_ data: Data) {
        lock.lock(); pendingAudio = data
        if writingAudio { lock.unlock(); return }
        writingAudio = true; lock.unlock()
        queue.async { [weak self] in
            guard let self else { return }
            while true {
                self.lock.lock()
                guard let audio = self.pendingAudio else { self.writingAudio = false; self.lock.unlock(); break }
                self.pendingAudio = nil; self.lock.unlock()
                if let message = try? JSONSerialization.data(withJSONObject: ["type": "audio", "pcm": audio.base64EncodedString()]) { self.write(message + Data([10])) }
            }
        }
    }
    private func write(_ data: Data) { try? input.fileHandleForWriting.write(contentsOf: data) }
    func stop() {
        onExit = nil; onEvent = nil
        if let p = process, p.isRunning { p.terminate() }
        try? input.fileHandleForWriting.close()
        process = nil
    }
    deinit { stop() }
}
