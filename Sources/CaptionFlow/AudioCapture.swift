import AVFoundation
import ScreenCaptureKit
import CoreMedia

final class AudioCapture: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    private var engine: AVAudioEngine?
    private var stream: SCStream?
    private var converter: AVAudioConverter?
    private let lock = NSLock()
    private var samples: [Float] = []
    private var active = false
    private let captureQueue = DispatchQueue(label: "captionflow.audio", qos: .userInitiated)
    var onAudio: (@Sendable (Data, Float) -> Void)?
    var onError: (@Sendable (String) -> Void)?

    func start(_ source: AudioSource) async throws {
        if source == .microphone {
            let granted = await AVCaptureDevice.requestAccess(for: .audio)
            guard granted else { throw failure("Microphone access is off. Enable CaptionFlow in System Settings → Privacy & Security → Microphone.") }
            try Task.checkCancellation()
            let engine = AVAudioEngine(); let input = engine.inputNode
            let format = input.outputFormat(forBus: 0)
            guard format.sampleRate > 0, format.channelCount > 0 else { throw failure("No microphone is available. Choose an input in System Settings → Sound.") }
            setActive(true)
            input.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, _ in self?.consume(buffer) }
            self.engine = engine
            do { try engine.start() } catch { await stop(); throw error }
        } else {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            try Task.checkCancellation()
            guard let display = content.displays.first else { throw failure("No display is available for Mac audio capture.") }
            let excluded = content.applications.filter { $0.bundleIdentifier == Bundle.main.bundleIdentifier }
            let filter = SCContentFilter(display: display, excludingApplications: excluded, exceptingWindows: [])
            let config = SCStreamConfiguration()
            config.width = 2; config.height = 2; config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
            config.capturesAudio = true; config.excludesCurrentProcessAudio = true
            config.sampleRate = 48_000; config.channelCount = 2
            let stream = SCStream(filter: filter, configuration: config, delegate: self)
            try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: captureQueue)
            self.stream = stream; setActive(true)
            do { try await stream.startCapture() } catch { await stop(); throw error }
        }
    }
    private func setActive(_ value: Bool) { lock.lock(); active = value; lock.unlock() }
    func stop() async {
        setActive(false)
        engine?.inputNode.removeTap(onBus: 0); engine?.stop(); engine = nil
        if let stream { try? await stream.stopCapture() }; stream = nil
        clear()
    }
    private func clear() { lock.lock(); converter = nil; samples.removeAll(); lock.unlock() }
    private func failure(_ message: String) -> NSError { NSError(domain: "Audio", code: 1, userInfo: [NSLocalizedDescriptionKey: message]) }
    private func consume(_ buffer: AVAudioPCMBuffer) {
        lock.lock(); defer { lock.unlock() }
        guard active else { return }
        let target = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16_000, channels: 1, interleaved: false)!
        if converter?.inputFormat != buffer.format { converter = AVAudioConverter(from: buffer.format, to: target) }
        guard let converter, let result = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: AVAudioFrameCount(Double(buffer.frameLength) * 16_000 / buffer.format.sampleRate + 32)) else { return }
        var supplied = false; var error: NSError?
        converter.convert(to: result, error: &error) { _, status in
            if supplied { status.pointee = .noDataNow; return nil }
            supplied = true; status.pointee = .haveData; return buffer
        }
        guard error == nil, let channel = result.floatChannelData?[0] else { return }
        samples.append(contentsOf: UnsafeBufferPointer(start: channel, count: Int(result.frameLength)))
        while samples.count >= 3200 {
            let frame = Array(samples.prefix(3200)); samples.removeFirst(3200)
            let rms = sqrt(frame.reduce(Float(0)) { $0 + $1 * $1 } / Float(frame.count))
            frame.withUnsafeBytes { onAudio?(Data($0), rms) }
        }
    }
    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sampleBuffer.isValid,
              let description = sampleBuffer.formatDescription else { return }
        let format = AVAudioFormat(cmAudioFormatDescription: description)
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(sampleBuffer.numSamples)) else { return }
        buffer.frameLength = buffer.frameCapacity
        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(sampleBuffer, at: 0, frameCount: Int32(sampleBuffer.numSamples), into: buffer.mutableAudioBufferList)
        if status == noErr { consume(buffer) }
    }
    func stream(_ stream: SCStream, didStopWithError error: Error) { onError?(error.localizedDescription) }
}
