import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'portable'))
import benchmark

class BudgetTests(unittest.TestCase):
    def test_short_budget_saves_active_trial(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.json';p.write_text('{}')
            def active(config,fixture,duration,mode,cancelled,checkpoint):
                checkpoint({'status':'running','audio_seconds':.05,'fixture_sha256':'test'})
                self.assertTrue(cancelled.wait(2))
                return {'status':'partial','partial':True,'audio_seconds':.1,'fixture_sha256':'test'}
            started=time.monotonic()
            with patch.object(benchmark,'hardware',return_value={}),patch.object(benchmark,'trial',side_effect=active):
                report=benchmark._run_suite([p],Path(__file__).resolve().parents[1]/'docs/fixtures/fr-synthetic.json',Path(d)/'out',hours=.1/3600,progress=lambda _:None)
            self.assertLess(time.monotonic()-started,1.5)
            self.assertEqual(report['trials'][0]['status'],'partial')
            self.assertEqual(report['trials'][0]['audio_seconds'],.1)

if __name__=='__main__':unittest.main()

class IncrementalUploadTests(unittest.TestCase):
    def test_long_report_is_batched_and_checkpoints_are_not_uploaded(self):
        import feedback
        with tempfile.TemporaryDirectory() as d:
            client=feedback.Feedback(d);client.consent(True);client.state['token']='test'
            report={'run_id':'test','trials':[{'id':str(i),'status':'ok'} for i in range(120)]+[{'id':'live','status':'running'}]}
            with patch.object(client,'request',return_value={}) as request:
                client.upload(report)
                self.assertEqual([len(call.args[1]['report']['trials']) for call in request.call_args_list],[50,50,20])
                client.upload(report);self.assertEqual(request.call_count,3)
                report['trials'][-1]['status']='partial'
                client.upload(report,force=False);self.assertEqual(request.call_count,3)
                client.upload(report);self.assertEqual(request.call_count,4)
                self.assertEqual(request.call_args.args[1]['report']['trials'],[{'id':'live','status':'partial'}])
