import Foundation
import Darwin
import CryptoKit

/// A private JSON-lines subprocess. No HTTP server or cloud inference.
final class WorkerBridge: @unchecked Sendable {
    private var process: Process?
    private let input = Pipe()
    private let queue = DispatchQueue(label: "captionflow.worker.write")
    private let lock = NSLock()
    private let maximumAudioBytes: Int
    private var queuedAudioBytes = 0
    private var acceptingAudio = false
    private var failed = false
    private let readerFinished = DispatchGroup()
    var onEvent: (@Sendable ([String: Any]) -> Void)?
    var onExit: (@Sendable (String) -> Void)?
    init(maximumAudioBytes: Int = 25 * 16000 * 4) {
        self.maximumAudioBytes = maximumAudioBytes
        signal(SIGPIPE, SIG_IGN)
    }
    static var support: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("CaptionFlow")
    }
    static var runtime: URL {
        let name = Preferences.supportsMLX ? "runtime" : "runtime-x86_64"
        return support.appendingPathComponent(Bundle.main.bundleIdentifier == "dev.captionflow.gpui" ? "GPUI/" + name : name)
    }
    static func bundledPython(in resources: URL?) -> URL? {
        guard let path = resources?.appendingPathComponent("python/bin/python3"), FileManager.default.isExecutableFile(atPath: path.path) else { return nil }
        return path
    }
    static var python: URL { bundledPython(in: Bundle.main.resourceURL) ?? runtime.appendingPathComponent("bin/python3") }
    static var resources: URL {
        if let resources = Bundle.main.url(forResource: "worker", withExtension: nil) { return resources }
        #if SWIFT_PACKAGE
        return Bundle.module.url(forResource: "worker", withExtension: nil)!
        #else
        fatalError("Missing bundled worker resources")
        #endif
    }
    static var isInstalled: Bool {
        bundledPython(in: Bundle.main.resourceURL) != nil || runtimeIsInstalled(at: runtime, requirements: resources.appendingPathComponent("requirements.txt"))
    }
    static func requirementsFingerprint(_ requirements: URL) throws -> Data {
        let digest = SHA256.hash(data: try Data(contentsOf: requirements))
        return Data(digest.map { String(format: "%02x", $0) }.joined().utf8)
    }
    static func runtimeIsInstalled(at runtime: URL, requirements: URL) -> Bool {
        guard FileManager.default.isExecutableFile(atPath: runtime.appendingPathComponent("bin/python3").path),
              let expected = try? requirementsFingerprint(requirements),
              let actual = try? Data(contentsOf: runtime.appendingPathComponent(".requirements-sha256")) else { return false }
        return actual == expected
    }

    static func install(using pythonPath: String, progress: @escaping @Sendable (String) -> Void) async throws {
        try await Task.detached {
            try FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
            let requirements = resources.appendingPathComponent("requirements.txt")
            let fingerprint = try requirementsFingerprint(requirements)
            let marker = runtime.appendingPathComponent(".requirements-sha256")
            if FileManager.default.fileExists(atPath: marker.path) { try FileManager.default.removeItem(at: marker) }
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
            try FileManager.default.createDirectory(at: runtime.deletingLastPathComponent(), withIntermediateDirectories: true)
            try run(pythonPath, ["-m", "venv", runtime.path])
            progress("Installing MLX speech and translation dependencies…")
            try run(python.path, ["-m", "pip", "install", "--disable-pip-version-check", "-r", requirements.path])
            try fingerprint.write(to: marker, options: .atomic)
        }.value
    }
    func start(configuration: [String: Any], executable: URL = WorkerBridge.python, arguments: [String]? = nil) throws {
        let p = Process(); p.executableURL = executable
        p.arguments = arguments ?? ["-u", Self.resources.appendingPathComponent("worker.py").path]
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
        lock.lock(); acceptingAudio = true; lock.unlock()
        readerFinished.enter()
        let readerFinished = self.readerFinished
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            defer { readerFinished.leave() }
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
        lock.lock()
        guard acceptingAudio else { lock.unlock(); return }
        guard queuedAudioBytes + data.count <= maximumAudioBytes else {
            acceptingAudio = false; lock.unlock()
            reportFailure("Audio delivery fell more than 25 seconds behind. Listening stopped to avoid losing speech.")
            return
        }
        queuedAudioBytes += data.count
        // Enqueue while locked so stop cannot overtake an accepted frame.
        queue.async { [self] in
            defer { lock.lock(); queuedAudioBytes -= data.count; lock.unlock() }
            do {
                let message = try JSONSerialization.data(withJSONObject: ["type": "audio", "pcm": data.base64EncodedString()])
                write(message + Data([10]))
            } catch { reportFailure("Could not encode captured audio: \(error.localizedDescription)") }
        }
        lock.unlock()
    }
    private func reportFailure(_ message: String) {
        lock.lock()
        guard !failed else { lock.unlock(); return }
        failed = true; acceptingAudio = false; lock.unlock()
        onEvent?(["type": "error", "message": message])
    }
    private func write(_ data: Data) {
        do { try input.fileHandleForWriting.write(contentsOf: data) }
        catch { reportFailure("Could not deliver audio to the inference worker: \(error.localizedDescription)") }
    }
    private func beginFinish() -> Process? {
        guard let p = process else { return nil }
        lock.lock(); acceptingAudio = false
        queue.async { [self] in
            write(Data("{\"type\":\"stop\"}\n".utf8))
            try? input.fileHandleForWriting.close()
        }
        lock.unlock()
        return p
    }
    func finish(timeout: TimeInterval = 60) async throws {
        guard let p = beginFinish() else { return }
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            DispatchQueue.global(qos: .userInitiated).async { [self] in
                guard readerFinished.wait(timeout: .now() + timeout) == .success else {
                    stop()
                    _ = readerFinished.wait(timeout: .now() + 2)
                    continuation.resume(throwing: NSError(domain: "Worker shutdown", code: 1, userInfo: [NSLocalizedDescriptionKey: "The inference worker did not finish draining within \(Int(timeout)) seconds."]))
                    return
                }
                guard p.terminationStatus == 0 else {
                    continuation.resume(throwing: NSError(domain: "Worker shutdown", code: Int(p.terminationStatus), userInfo: [NSLocalizedDescriptionKey: "The inference worker exited before shutdown completed (\(p.terminationStatus))."]))
                    return
                }
                continuation.resume()
            }
        }
        process = nil
    }
    func stop() {
        lock.lock(); acceptingAudio = false; lock.unlock()
        onExit = nil; onEvent = nil
        if let p = process, p.isRunning {
            p.terminate()
            DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 1) {
                if p.isRunning { Darwin.kill(p.processIdentifier, SIGKILL) }
            }
        }
        let handle = input.fileHandleForWriting
        queue.async { try? handle.close() }
        process = nil
    }
    deinit { stop() }
}
