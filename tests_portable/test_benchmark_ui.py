import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'portable'))
from PySide6.QtWidgets import QApplication
import app
import benchmark

class BenchmarkUITests(unittest.TestCase):
    def test_start_prepares_cpu_when_gpu_setup_fails_without_prompt(self):
        application=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as d:
            window=app.Window(d,smoke_test=True)
            requested=window.backend.currentText().lower()
            def install(directory,backend,**kwargs):
                self.assertFalse(kwargs['allow_network'])
                if backend!= 'cpu': raise OSError('Simulated GPU setup failure')
                return {'native_backend':'cpu','targets':[]}
            with patch.object(app,'install',side_effect=install) as setup,patch.object(benchmark,'run_suite',return_value={}) as run,patch('urllib.request.urlopen') as network:
                window.start_benchmark()
                window.task.join(timeout=3);self.assertFalse(window.task.is_alive())
                application.processEvents()
                self.assertEqual([call.args[1] for call in setup.call_args_list],[requested,'cpu'])
                profiles=run.call_args.args[0]
                self.assertEqual(len(profiles),6)
                self.assertTrue(all(json.loads(Path(p).read_text())['native_backend']=='cpu' for p in profiles))
                self.assertIsNone(run.call_args.kwargs['hours'])
                network.assert_not_called()
            window.close()

if __name__=='__main__':unittest.main()

class MixedCaptureTests(unittest.TestCase):
    def test_mixed_audio_uses_exactly_one_worker(self):
        from unittest.mock import MagicMock
        import base64
        application=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as d:
            window=app.Window(d,smoke_test=True)
            session=MagicMock();session.process.returncode=0
            capture=MagicMock();pcm=b'\x00'*6400
            calls=[]
            def next_frame(cancelled):
                if calls:
                    cancelled.set();return None
                calls.append(True);return pcm
            capture.next.side_effect=next_frame
            with patch.object(app,'doctor'),patch.object(app,'WorkerSession',return_value=session) as worker,patch('audio_pipeline.AudioProcess',return_value=capture) as helper:
                window.capture({},'mixed')
                worker.assert_called_once()
                self.assertEqual(helper.call_args.args[0],'mixed')
                session.send.assert_called_once_with({'type':'audio','pcm':base64.b64encode(pcm).decode()})
                capture.close.assert_called_once()
            window.close()
