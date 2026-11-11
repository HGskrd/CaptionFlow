import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portable'))
from audio_pipeline import Mixer, AudioProcess

class MixerTests(unittest.TestCase):
    def test_two_sources_produce_one_timeline_and_preserve_overlap(self):
        mixer=Mixer(0,echo=False)
        try:
            for i in range(100):
                stamp=i*.01
                mixer.push(0,np.full(160,.1),stamp)
                mixer.push(1,np.full(160,.2),stamp)
                frame=mixer.pull(stamp)
                self.assertEqual(len(frame),160)
                np.testing.assert_allclose(frame,.3,atol=1e-6)
            self.assertEqual(mixer.stats()['frames'],100)
            self.assertEqual(mixer.stats()['missing_mic'],0)
        finally:mixer.close()
    def test_silent_output_does_not_block_microphone(self):
        mixer=Mixer(0,echo=False)
        try:
            mixer.push(0,np.full(160,.25),0)
            np.testing.assert_allclose(mixer.pull(0),.25)
            self.assertEqual(mixer.stats()['missing_system'],160)
        finally:mixer.close()
    def test_limiter_nonfinite_and_stale_buffers(self):
        mixer=Mixer(0,echo=False)
        try:
            for source in (0,1):mixer.push(source,np.full(160,.9),0)
            self.assertLessEqual(float(abs(mixer.pull(0)).max()),.951)
            np.testing.assert_array_equal(mixer.pull(32768/16000),np.zeros(160))
            mixer.push(0,np.full(160,np.nan),10)
            self.assertTrue(np.isfinite(mixer.pull(10)).all())
            self.assertGreater(mixer.stats()['resyncs'],0)
        finally:mixer.close()
    def test_device_clock_drift_stays_bounded(self):
        mixer=Mixer(0,echo=False)
        try:
            for i in range(10000):
                mixer.push(0,np.full(160,.2),i*.01001)
                mixer.push(1,np.full(160,.2),i*.01)
                mixer.pull(max(0,i*.01-.15))
            stats=mixer.stats()
            self.assertGreater(stats['drift_samples'],0)
            self.assertEqual(stats['resyncs'],0)
            self.assertLess(stats['missing_mic'],16000)
        finally:mixer.close()
    def test_echo_reduction_and_near_end_survives(self):
        # Known speaker leakage with a 30ms acoustic delay; independent near speech.
        rng=np.random.default_rng(7); n=16000*15
        far=(rng.normal(0,.12,n)).astype(np.float32)
        near=(rng.normal(0,.04,n)).astype(np.float32);near[:16000*10]=0
        mic=np.zeros(n,dtype=np.float32);mic[480:]=far[:-480]*.6;mic+=near
        mixer=Mixer(0,echo=True);out=[]
        try:
            for offset in range(0,n,160):
                mixer.push(0,mic[offset:offset+160],offset/16000)
                mixer.push(1,far[offset:offset+160],offset/16000)
                out.append(mixer.pull(offset/16000))
            result=np.concatenate(out)-far
            residual=float(np.mean(result[16000*7:16000*9]**2))
            baseline=float(np.mean(mic[16000*7:16000*9]**2))
            self.assertLess(residual,baseline*.1) # >10 dB echo reduction
            delayed=near[16000*12-800:16000*14-800]
            correlation=np.corrcoef(result[16000*12:16000*14],delayed)[0,1]
            self.assertGreater(correlation,.6) # Preserve independent overlapping input
        finally:mixer.close()

class CaptureWatchdogTests(unittest.TestCase):
    def test_stall_and_cancel_do_not_wait_forever(self):
        process=AudioProcess('mixed',Path('unused'))
        with self.assertRaisesRegex(RuntimeError,'stalled'):
            process.next(threading.Event(),timeout=.01)
        stop=threading.Event();stop.set()
        self.assertIsNone(process.next(stop))
    def test_child_failure_is_detected(self):
        process=AudioProcess('mixed',Path('unused'));process.done.set()
        with self.assertRaisesRegex(RuntimeError,'capture stopped'):
            process.next(threading.Event())

class ProcessIsolationTests(unittest.TestCase):
    def test_wedged_capture_child_is_terminated(self):
        import subprocess
        real_popen=subprocess.Popen
        def sleeper(*args,**kwargs):
            return real_popen([sys.executable,'-c','import time;time.sleep(60)'],**kwargs)
        capture=AudioProcess('mixed',Path('unused'))
        with patch('audio_pipeline.subprocess.Popen',side_effect=sleeper):
            capture.start()
        try:
            with self.assertRaisesRegex(RuntimeError,'stalled'):capture.next(threading.Event(),timeout=.05)
        finally:capture.close()
        self.assertIsNotNone(capture.process.poll())
        self.assertFalse(capture.reader.is_alive())
