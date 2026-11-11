#include "CaptionAudio.h"
#include "speex/speex_echo.h"
#include "speex/speex_preprocess.h"
#include <stdlib.h>
#include <math.h>
#include <string.h>
#include <limits.h>
#define RATE 16000
#define FRAME 160
#define RING 32768
#define MIC_DELAY 640
struct CFAudio {
    double origin, timing_error[2];
    int mode, started[2], delay_index;
    int64_t next[2], stamps[2][RING];
    float data[2][RING], delay[MIC_DELAY], gain;
    SpeexEchoState *echo;
    SpeexPreprocessState *preprocess;
    CFAudioStats stats;
};
static float safe(float v) { return isfinite(v) ? fmaxf(-1, fminf(1, v)) : 0; }
CFAudio *cf_audio_create(double origin, int mode, int echo) {
    if (!isfinite(origin) || mode < 1 || mode > 3) return NULL;
    CFAudio *s = calloc(1, sizeof(*s));
    if (!s) return NULL;
    s->origin = origin; s->mode = mode; s->gain = 1;
    for (int c=0;c<2;c++) for (int i=0;i<RING;i++) s->stamps[c][i]=INT64_MIN;
    if (mode == 3 && echo) {
        s->echo = speex_echo_state_init(FRAME, RATE/2);
        s->preprocess = speex_preprocess_state_init(FRAME, RATE);
        if (!s->echo || !s->preprocess) { cf_audio_destroy(s); return NULL; }
        int rate=RATE, off=0;
        speex_echo_ctl(s->echo, SPEEX_ECHO_SET_SAMPLING_RATE, &rate);
        speex_preprocess_ctl(s->preprocess, SPEEX_PREPROCESS_SET_DENOISE, &off);
        speex_preprocess_ctl(s->preprocess, SPEEX_PREPROCESS_SET_AGC, &off);
        speex_preprocess_ctl(s->preprocess, SPEEX_PREPROCESS_SET_ECHO_STATE, s->echo);
    }
    return s;
}
void cf_audio_destroy(CFAudio *s) {
    if (!s) return;
    if (s->preprocess) speex_preprocess_state_destroy(s->preprocess);
    if (s->echo) speex_echo_state_destroy(s->echo);
    free(s);
}
void cf_audio_push(CFAudio *s, int c, const float *pcm, int n, double start) {
    if (!s || c<0 || c>1 || !pcm || n<=0 || n>=RING/2 || !isfinite(start)) return;
    int64_t observed = llround((start-s->origin)*RATE);
    if (!s->started[c]) { s->next[c]=observed; s->started[c]=1; }
    double difference = (double)(observed-s->next[c]);
    int count=n;
    if (fabs(difference)>RATE/5) {
        s->next[c]=observed; s->timing_error[c]=0; s->stats.resyncs++;
        for (int i=0;i<RING;i++) s->stamps[c][i]=INT64_MIN;
        if (s->echo) speex_echo_state_reset(s->echo);
    } else {
        /* Smooth scheduling jitter; resample a single sample per block to follow
           independent device clocks, without unbounded queues or periodic jumps. */
        s->timing_error[c] = .99*s->timing_error[c]+.01*difference;
        if (s->timing_error[c]>160) { count++; s->stats.drift_samples++; }
        if (s->timing_error[c]<-160) { count--; s->stats.drift_samples++; }
    }
    for (int i=0;i<count;i++) {
        double pos = count>1 ? (double)i*(n-1)/(count-1) : 0;
        int a=(int)pos, b=a+1<n?a+1:a;
        float value=safe(pcm[a])+(safe(pcm[b])-safe(pcm[a]))*(float)(pos-a);
        int64_t index=s->next[c]+i; unsigned slot=(unsigned)(index & (RING-1));
        s->data[c][slot]=value; s->stamps[c][slot]=index;
    }
    s->next[c]+=count;
}
void cf_audio_pull(CFAudio *s, double start, float *output) {
    if (!s || !output || !isfinite(start)) return;
    int64_t index=llround((start-s->origin)*RATE);
    float mic[FRAME], system[FRAME], clean[FRAME], mic_energy=0, system_energy=0, clean_energy=0;
    for (int i=0;i<FRAME;i++) {
        unsigned slot=(unsigned)((index+i)&(RING-1)); float values[2]={0,0};
        for (int c=0;c<2;c++) if (s->mode & (1<<c)) {
            if (s->stamps[c][slot]==index+i) values[c]=s->data[c][slot];
            else if(c==0) s->stats.missing_mic++; else s->stats.missing_system++;
        }
        mic[i]=values[0]; system[i]=values[1];
        mic_energy+=mic[i]*mic[i]; system_energy+=system[i]*system[i];
    }
    if (s->echo) {
        spx_int16_t near[FRAME], far[FRAME], result[FRAME];
        for (int i=0;i<FRAME;i++) {
            /* Give the reference a 40ms lead even when capture callbacks jitter. */
            near[i]=(spx_int16_t)(safe(s->delay[s->delay_index])*32767);
            s->delay[s->delay_index]=mic[i]; s->delay_index=(s->delay_index+1)%MIC_DELAY;
            far[i]=(spx_int16_t)(system[i]*32767);
        }
        speex_echo_cancellation(s->echo, near, far, result);
        speex_preprocess_run(s->preprocess, result);
        for (int i=0;i<FRAME;i++) clean[i]=(float)result[i]/32768;
    } else memcpy(clean,mic,sizeof(mic));
    float peak=0;
    for (int i=0;i<FRAME;i++) {
        clean_energy+=clean[i]*clean[i]; output[i]=clean[i]+system[i];
        peak=fmaxf(peak,fabsf(output[i]));
    }
    float target=peak>.95f?.95f/peak:1;
    s->gain=fminf(target,s->gain+.005f); /* Instant attack, ~2 second release. */
    if(s->gain<.999f) s->stats.limited_frames++;
    for(int i=0;i<FRAME;i++) output[i]*=s->gain;
    s->stats.frames++;
    s->stats.mic_rms=sqrtf(mic_energy/FRAME); s->stats.system_rms=sqrtf(system_energy/FRAME);
    s->stats.cleaned_mic_rms=sqrtf(clean_energy/FRAME); s->stats.output_peak=peak*s->gain;
}
void cf_audio_stats(CFAudio *s, CFAudioStats *stats) { if(s && stats) *stats=s->stats; }
