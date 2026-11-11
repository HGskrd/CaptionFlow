#ifndef CAPTION_AUDIO_H
#define CAPTION_AUDIO_H
#include <stdint.h>
#ifdef _WIN32
#define CF_API __declspec(dllexport)
#else
#define CF_API __attribute__((visibility("default")))
#endif
#ifdef __cplusplus
extern "C" {
#endif
/* All calls for an instance must be serialized. PCM is mono float at 16 kHz.
   Timestamps use one monotonic clock, in seconds. source: 0 mic, 1 output.
   mode: 1 mic, 2 output, 3 both. pull always returns one 10 ms frame. */
typedef struct CFAudio CFAudio;
typedef struct {
    uint64_t frames, missing_mic, missing_system, resyncs, drift_samples, limited_frames;
    float mic_rms, system_rms, cleaned_mic_rms, output_peak;
} CFAudioStats;
CF_API CFAudio *cf_audio_create(double origin, int mode, int echo);
CF_API void cf_audio_destroy(CFAudio *s);
CF_API void cf_audio_push(CFAudio *s, int source, const float *pcm, int count, double start);
CF_API void cf_audio_pull(CFAudio *s, double start, float *output);
CF_API void cf_audio_stats(CFAudio *s, CFAudioStats *stats);
#ifdef __cplusplus
}
#endif
#endif
