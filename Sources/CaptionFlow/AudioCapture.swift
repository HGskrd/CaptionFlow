import AVFoundation
import ScreenCaptureKit
import CoreMedia
import CaptionAudio

final class AudioCapture: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    private var engine: AVAudioEngine?
    private var stream: SCStream?
    private var converters: [Int: AVAudioConverter] = [:]
    private let lock = NSLock()
    private var mixer: OpaquePointer?
    private var samples: [Float] = []
    private var active = false
    private var timer: DispatchSourceTimer?
    private var nextFrame = 0.0
    private var lastMicrophone = 0.0
    private var needsMicrophone = false
    private var diagnosticsURL: URL?
    private var lastDiagnostic = 0.0
    private let captureQueue = DispatchQueue(label: "captionflow.audio", qos: .userInitiated)
    var onAudio: (@Sendable (Data, Float) -> Void)?
    var onError: (@Sendable (String) -> Void)?

    private func now() -> Double { CMClockGetTime(CMClockGetHostTimeClock()).seconds }
    func start(_ source: AudioSource) async throws {
        if source != .system {
            let granted = await AVCaptureDevice.requestAccess(for: .audio)
            guard granted else { throw failure("Microphone access is off. Enable CaptionFlow in System Settings → Privacy & Security → Microphone.") }
        }
        try Task.checkCancellation()
        // Resolve system permission before starting either source.
        var filter: SCContentFilter?
        if source != .microphone {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            guard let display = content.displays.first else { throw failure("No display is available for Mac audio capture.") }
            let excluded = content.applications.filter { $0.bundleIdentifier == Bundle.main.bundleIdentifier }
            filter = SCContentFilter(display: display, excludingApplications: excluded, exceptingWindows: [])
        }
        try Task.checkCancellation()
        try prepare(source)
        do {
            if source != .system {
                let engine = AVAudioEngine(); let input = engine.inputNode
                let format = input.outputFormat(forBus: 0)
                guard format.sampleRate > 0, format.channelCount > 0 else { throw failure("No microphone is available. Choose an input in System Settings → Sound.") }
                input.installTap(onBus: 0, bufferSize: 960, format: format) { [weak self] buffer, time in
                    guard let self else { return }
                    let stamp = time.isHostTimeValid ? AVAudioTime.seconds(forHostTime: time.hostTime) : self.now() - Double(buffer.frameLength)/buffer.format.sampleRate
                    self.consume(buffer, source: 0, start: stamp)
                }
                self.engine = engine
                try engine.start()
            }
            if let filter {
                let config = SCStreamConfiguration()
                config.width = 2; config.height = 2; config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
                config.capturesAudio = true; config.excludesCurrentProcessAudio = true
                config.sampleRate = 48_000; config.channelCount = 2
                let stream = SCStream(filter: filter, configuration: config, delegate: self)
                try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: captureQueue)
                self.stream = stream
                try await stream.startCapture()
            }
            try Task.checkCancellation()
            beginTimer()
        } catch { await stop(); throw error }
    }
    private func prepare(_ source: AudioSource) throws {
        lock.lock(); defer { lock.unlock() }
        mixer = cf_audio_create(now(), source == .mixed ? 3 : source == .microphone ? 1 : 2, 1)
        guard mixer != nil else { throw failure("Could not initialize audio mixer.") }
        active = true; needsMicrophone = source != .system; lastMicrophone = now()
        let directory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("CaptionFlow/diagnostics")
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        diagnosticsURL = directory.appendingPathComponent("audio-latest.json")
    }
    private func beginTimer() {
        lock.lock(); nextFrame = now(); lastMicrophone = now(); lock.unlock()
        let timer = DispatchSource.makeTimerSource(queue: captureQueue)
        timer.schedule(deadline: .now() + .milliseconds(150), repeating: .milliseconds(10), leeway: .milliseconds(2))
        timer.setEventHandler { [weak self] in self?.tick() }
        self.timer = timer; timer.resume()
    }
    private func tick() {
        lock.lock()
        guard active, let mixer else { lock.unlock(); return }
        let clock = now()
        if clock-nextFrame > 0.65 || (needsMicrophone && clock-lastMicrophone > 3) {
            active = false; writeDiagnostics("failed"); lock.unlock()
            onError?("Audio capture stalled. Reconnect your audio device and start again. Audio metrics were saved in CaptionFlow/diagnostics.")
            return
        }
        var packets: [(Data, Float)] = []
        // Catch up bounded timer jitter without stretching or duplicating audio.
        while nextFrame + 0.15 <= clock {
            var frame = [Float](repeating: 0, count: 160)
            cf_audio_pull(mixer, nextFrame, &frame); nextFrame += 0.01
            samples.append(contentsOf: frame)
            if samples.count >= 1600 {
                let packet = Array(samples.prefix(1600)); samples.removeFirst(1600)
                let rms = sqrt(packet.reduce(Float(0)) { $0 + $1*$1 } / Float(packet.count))
                packets.append((packet.withUnsafeBytes { Data($0) }, rms))
            }
        }
        if clock-lastDiagnostic > 2 { writeDiagnostics("capturing"); lastDiagnostic = clock }
        lock.unlock()
        for (data, rms) in packets { onAudio?(data, rms) }
    }
    private func writeDiagnostics(_ status: String) {
        guard let mixer, let diagnosticsURL else { return }
        var stats = CFAudioStats(); cf_audio_stats(mixer, &stats)
        let report: [String: Any] = ["schema": 1, "status": status, "inference_streams": 1,
            "frames": stats.frames, "missing_mic": stats.missing_mic, "missing_system": stats.missing_system,
            "resyncs": stats.resyncs, "drift_samples": stats.drift_samples, "limited_frames": stats.limited_frames,
            "mic_rms": stats.mic_rms, "system_rms": stats.system_rms, "cleaned_mic_rms": stats.cleaned_mic_rms,
            "output_peak": stats.output_peak, "buffer_seconds": 0.15]
        if let data = try? JSONSerialization.data(withJSONObject: report, options: .prettyPrinted) { try? data.write(to: diagnosticsURL, options: .atomic) }
    }
    func stop() async {
        deactivate()
        timer?.cancel(); timer = nil
        engine?.inputNode.removeTap(onBus: 0); engine?.stop(); engine = nil
        if let stream { try? await stream.stopCapture() }; stream = nil
        clear()
    }
    private func deactivate() { lock.lock(); writeDiagnostics(active ? "stopped" : "failed"); active = false; lock.unlock() }
    private func clear() {
        lock.lock(); defer { lock.unlock() }
        if let mixer { cf_audio_destroy(mixer) }; mixer = nil
        converters.removeAll(); samples.removeAll()
    }
    private func failure(_ message: String) -> NSError { NSError(domain: "Audio", code: 1, userInfo: [NSLocalizedDescriptionKey: message]) }
    private func consume(_ buffer: AVAudioPCMBuffer, source: Int, start: Double) {
        lock.lock(); defer { lock.unlock() }
        guard active, let mixer else { return }
        let target = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16_000, channels: 1, interleaved: false)!
        if converters[source]?.inputFormat != buffer.format { converters[source] = AVAudioConverter(from: buffer.format, to: target) }
        guard let converter = converters[source], let result = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: AVAudioFrameCount(Double(buffer.frameLength) * 16_000 / buffer.format.sampleRate + 32)) else { return }
        var supplied = false; var error: NSError?
        converter.convert(to: result, error: &error) { _, status in
            if supplied { status.pointee = .noDataNow; return nil }
            supplied = true; status.pointee = .haveData; return buffer
        }
        guard error == nil, let channel = result.floatChannelData?[0] else { return }
        cf_audio_push(mixer, Int32(source), channel, Int32(result.frameLength), start)
        if source == 0 { lastMicrophone = now() }
    }
    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sampleBuffer.isValid,
              let description = sampleBuffer.formatDescription else { return }
        let format = AVAudioFormat(cmAudioFormatDescription: description)
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(sampleBuffer.numSamples)) else { return }
        buffer.frameLength = buffer.frameCapacity
        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(sampleBuffer, at: 0, frameCount: Int32(sampleBuffer.numSamples), into: buffer.mutableAudioBufferList)
        if status == noErr { consume(buffer, source: 1, start: sampleBuffer.presentationTimeStamp.seconds) }
    }
    func stream(_ stream: SCStream, didStopWithError error: Error) { onError?(error.localizedDescription) }
}
