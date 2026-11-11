import XCTest
import CaptionAudio

final class AudioMixerTests: XCTestCase {
    func testTwoInputsProduceOneFrame() throws {
        let mixer = try XCTUnwrap(cf_audio_create(0, 3, 0))
        defer { cf_audio_destroy(mixer) }
        let microphone = [Float](repeating: 0.1, count: 160)
        let system = [Float](repeating: 0.2, count: 160)
        var output = [Float](repeating: 0, count: 160)
        cf_audio_push(mixer, 0, microphone, 160, 0)
        cf_audio_push(mixer, 1, system, 160, 0)
        cf_audio_pull(mixer, 0, &output)
        XCTAssertTrue(output.allSatisfy { abs($0 - 0.3) < 0.00001 })
        var stats = CFAudioStats(); cf_audio_stats(mixer, &stats)
        XCTAssertEqual(stats.frames, 1)
        XCTAssertEqual(stats.missing_mic, 0)
        XCTAssertEqual(stats.missing_system, 0)
    }
    func testAbsentSystemAudioDoesNotBlockMicrophone() throws {
        let mixer = try XCTUnwrap(cf_audio_create(0, 3, 0))
        defer { cf_audio_destroy(mixer) }
        let microphone = [Float](repeating: 0.25, count: 160)
        var output = [Float](repeating: 0, count: 160)
        cf_audio_push(mixer, 0, microphone, 160, 0)
        cf_audio_pull(mixer, 0, &output)
        XCTAssertTrue(output.allSatisfy { $0 == 0.25 })
    }
}
