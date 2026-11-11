import base64
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'portable'))
import benchmark
import feedback
import runtime_assets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
spec = importlib.util.spec_from_file_location('report_server', ROOT / 'service/server.py')
server = importlib.util.module_from_spec(spec); spec.loader.exec_module(server)


class MetricsTests(unittest.TestCase):
    def test_percentiles_and_drift(self):
        self.assertIsNone(benchmark.percentile([], .95))
        self.assertAlmostEqual(benchmark.percentile([0, 1, 2, 3, 4], .95), 3.8)
        self.assertAlmostEqual(benchmark.slope([(1, 2), (2, 4), (3, 6)]), 2)
        self.assertEqual(benchmark.word_error('Bonjour, le monde!', 'bonjour le monde'), 0)
        self.assertEqual(benchmark.word_error('a b', 'a c d'), 1)

    def test_fixture_integrity(self):
        manifest, samples = benchmark.load_fixture(ROOT / 'docs/fixtures/fr-synthetic.json')
        self.assertGreater(len(samples), 16000)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'bad.json'; p.write_text(json.dumps({**manifest, 'audio':str(ROOT/'docs/fixtures/fr-synthetic.wav'), 'sha256':'bad'}))
            with self.assertRaises(ValueError): benchmark.load_fixture(p)

    def test_report_deduplication(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'report.json'; result = Path(d) / 'summary.json'
            p.write_text(json.dumps({'schema':1,'run_id':'one','hardware':{'cpu':'test'},'trials':[
                {'id':'one','profile':{},'fixture_sha256':'a','status':'ok','realtime_gate':True,'audio_seconds':10}]}))
            benchmark.aggregate([p,p], result)
            self.assertEqual(json.loads(result.read_text())['capabilities'][0]['trials'], 1)

    def test_resume_does_not_rerun_completed_trial(self):
        with tempfile.TemporaryDirectory() as d:
            config = Path(d)/'config.json'; config.write_text('{}')
            fixture = ROOT/'docs/fixtures/fr-synthetic.json'; output = Path(d)/'results'
            stop = threading.Event(); calls=[]
            def fake(*args, **kwargs):
                calls.append(args[3]); stop.set()
                return {'status':'ok','audio_seconds':1,'fixture_sha256':'test'}
            with patch.object(benchmark,'trial',side_effect=fake), patch.object(benchmark,'hardware',return_value={'cpu':'test'}):
                benchmark._run_suite([config],fixture,output,.01,1,stop,lambda _:None)
                stop.clear()
                benchmark._run_suite([config],fixture,output,.01,1,stop,lambda _:None)
            self.assertEqual(calls,['throughput','realtime'])
            with contextlib.closing(sqlite3.connect(next(output.glob('*/journal.sqlite')))) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM trials').fetchone()[0],2)


class PrivacyTests(unittest.TestCase):
    def test_no_request_when_unknown_or_declined(self):
        with tempfile.TemporaryDirectory() as d, patch('urllib.request.urlopen') as network:
            client=feedback.Feedback(d)
            for choice in (None,False):
                if choice is not None: client.consent(choice)
                with self.assertRaises(PermissionError): client.request('/health')
                with self.assertRaises(PermissionError): client.enroll()
            network.assert_not_called()

    def test_download_offline_can_use_verified_cache(self):
        import hashlib
        with tempfile.TemporaryDirectory() as d, patch('urllib.request.urlopen') as network:
            path=Path(d)/'model'; path.write_bytes(b'cached')
            runtime_assets.download('https://example.org',path,hashlib.sha256(b'cached').hexdigest(),allow_network=False)
            with self.assertRaises(PermissionError): runtime_assets.download('https://example.org',path,'bad',allow_network=False)
            network.assert_not_called()

    def test_report_removes_private_text(self):
        clean=feedback.public_report({'trials':[{'error':'private words','diagnostic_tail':['private'], 'word_error_rate':.1}], 'audio':'secret'})
        self.assertEqual(clean, {'trials':[{'word_error_rate':.1}]})

    def test_revocation_disables_even_if_service_unreachable(self):
        with tempfile.TemporaryDirectory() as d:
            client=feedback.Feedback(d);client.consent(True);client.state['token']='secret'
            with patch.object(client,'request',side_effect=OSError):
                with self.assertRaises(RuntimeError):client.revoke()
            self.assertFalse(client.allowed);self.assertNotIn('token',client.state)

    def test_update_signature_origin_expiry_and_platform(self):
        key=Ed25519PrivateKey.generate(); public=base64.b64encode(key.public_key().public_bytes_raw()).decode()
        release={'platform':'windows-x86_64','sequence':2,'version':'test','expires':1000,
                 'url':feedback.BASE_URL+'/releases/test.zip','sha256':'a'*64}
        def sign(value):
            payload=json.dumps(value).encode()
            return {'payload':base64.b64encode(payload).decode(),'signature':base64.b64encode(key.sign(payload)).decode()}
        self.assertEqual(feedback.verify_manifest(sign(release),'windows-x86_64',public,now=1),release)
        for changed in ({**release,'expires':0},{**release,'platform':'wrong'}, {**release,'url':'https://evil.example/file.zip'}):
            with self.assertRaises(ValueError): feedback.verify_manifest(sign(changed),'windows-x86_64',public,now=1)
        envelope=sign(release);envelope['payload']=base64.b64encode(b'{}').decode()
        with self.assertRaises(Exception): feedback.verify_manifest(envelope,'windows-x86_64',public,now=1)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.saved=server.STATE; server.STATE=Path(self.temp.name)
        server.RATE.clear(); server.initialize()
        self.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.http.server_port}'+server.PREFIX
    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join();server.STATE=self.saved;self.temp.cleanup()
    def request(self,path,body=None,token=None,method=None):
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        request=urllib.request.Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method)
        with urllib.request.urlopen(request) as response:return json.load(response)
    def test_enroll_upload_dedup_delete_and_no_public_read(self):
        token=self.request('/v1/enroll',{'consent_version':1})['token']
        payload={'schema':1,'app_version':'test','report':{'trials':[]}}
        with self.assertRaises(urllib.error.HTTPError) as error:self.request('/v1/reports',payload)
        self.assertEqual(error.exception.code,401)
        for _ in range(2):self.assertTrue(self.request('/v1/reports',payload,token)['accepted'])
        with server.connection() as conn:self.assertEqual(conn.execute('SELECT count(*) FROM reports').fetchone()[0],1)
        with self.assertRaises(urllib.error.HTTPError) as error:self.request('/v1/reports',{'schema':1,'transcript':'private'},token)
        self.assertEqual(error.exception.code,400)
        with self.assertRaises(urllib.error.HTTPError):self.request('/v1/reports')
        self.assertTrue(self.request('/v1/self',token=token,method='DELETE')['deleted'])
        with server.connection() as conn:self.assertEqual(conn.execute('SELECT count(*) FROM reports').fetchone()[0],0)




class UpdateRetryTests(unittest.TestCase):
    def test_downloaded_update_remains_offered_until_installed(self):
        with tempfile.TemporaryDirectory() as d:
            client=feedback.Feedback(d);client.consent(True);client.state['highest_release']=feedback.RELEASE_SEQUENCE+1
            release={'sequence':feedback.RELEASE_SEQUENCE+1}
            with patch.object(client,'request',return_value={'payload':'mock'}),patch.object(feedback,'verify_manifest',return_value=release):
                self.assertEqual(client.check_update(),release)
            with patch.object(client,'request',return_value={'payload':'mock'}),patch.object(feedback,'verify_manifest',return_value={'sequence':1}):
                self.assertIsNone(client.check_update())

class ResilientBenchmarkTests(unittest.TestCase):
    def settings(self, directory, backends=('metal',)):
        files=[]
        for i, backend in enumerate(backends):
            path=Path(directory)/f'config{i}.json';path.write_text(json.dumps({'native_backend':backend}));files.append(path)
        return files, ROOT/'docs/fixtures/fr-synthetic.json', Path(directory)/'results'

    def test_partial_is_saved_and_remaining_work_resumes(self):
        with tempfile.TemporaryDirectory() as d:
            configs, fixture, output=self.settings(d);stop=threading.Event();calls=[]
            def first(config, fixture, duration, mode, cancelled, checkpoint):
                calls.append((mode,duration))
                if mode=='realtime':
                    checkpoint({'status':'running','audio_seconds':8,'fixture_sha256':'test'})
                    cancelled.set()
                    return {'status':'partial','partial':True,'audio_seconds':8,'fixture_sha256':'test'}
                return {'status':'ok','audio_seconds':6,'fixture_sha256':'test'}
            with patch.object(benchmark,'hardware',return_value={}),patch.object(benchmark,'trial',side_effect=first):
                report=benchmark._run_suite(configs,fixture,output,trial_seconds=60,cancelled=stop,progress=lambda _:None)
            self.assertEqual(report['trials'][-1]['status'],'partial');stop.clear()
            def second(config, fixture, duration, mode, cancelled, checkpoint):
                calls.append((mode,duration));cancelled.set()
                return {'status':'ok','audio_seconds':duration,'fixture_sha256':'test'}
            with patch.object(benchmark,'hardware',return_value={}),patch.object(benchmark,'trial',side_effect=second):
                resumed=benchmark._run_suite(configs,fixture,output,trial_seconds=60,cancelled=stop,progress=lambda _:None)
            self.assertEqual(report['run_id'],resumed['run_id'])
            self.assertEqual(calls,[('throughput',60),('realtime',60),('realtime',52)])
            self.assertEqual(len(resumed['trials']),3)

    def test_startup_failure_skips_other_gpu_profiles_and_tries_cpu(self):
        with tempfile.TemporaryDirectory() as d:
            configs, fixture, output=self.settings(d,('vulkan','vulkan','cpu'));calls=[];stop=threading.Event()
            def fake(config,*args,**kwargs):
                calls.append(config['native_backend'])
                if config['native_backend']=='vulkan':
                    return {'status':'error','failure_stage':'probe','error':'GPU unavailable','audio_seconds':0,'fixture_sha256':'test'}
                stop.set();return {'status':'ok','audio_seconds':6,'fixture_sha256':'test'}
            with patch.object(benchmark,'hardware',return_value={}),patch.object(benchmark,'trial',side_effect=fake):
                result=benchmark._run_suite(configs,fixture,output,cancelled=stop,progress=lambda _:None)
            self.assertEqual(calls,['vulkan','cpu']);self.assertEqual(len(result['disabled_profiles']),2)

    def test_all_fail_stops_without_retry_loop(self):
        with tempfile.TemporaryDirectory() as d:
            configs, fixture, output=self.settings(d)
            with patch.object(benchmark,'hardware',return_value={}),patch.object(benchmark,'trial',return_value={'status':'error','audio_seconds':0,'fixture_sha256':'test'}) as run:
                result=benchmark._run_suite(configs,fixture,output,progress=lambda _:None)
                benchmark._run_suite(configs,fixture,output,progress=lambda _:None)
            self.assertEqual(run.call_count,1);self.assertEqual(len(result['trials']),1)

    def test_update_preserves_old_history_and_starts_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            configs, fixture, output=self.settings(d);stop=threading.Event()
            def fake(*args,**kwargs):
                stop.set();return {'status':'ok','audio_seconds':1,'fixture_sha256':'test'}
            with patch.object(benchmark,'hardware',return_value={}),patch.object(benchmark,'trial',side_effect=fake):
                before=benchmark._run_suite(configs,fixture,output,cancelled=stop,progress=lambda _:None);stop.clear()
                with patch.object(benchmark.platform,'python_version',return_value='updated-runtime'):
                    after=benchmark._run_suite(configs,fixture,output,cancelled=stop,progress=lambda _:None)
            self.assertNotEqual(before['run_id'],after['run_id']);self.assertEqual(len(list(output.glob('*/report.json'))),2)

    def test_crash_checkpoint_survives_and_profile_is_quarantined(self):
        with tempfile.TemporaryDirectory() as d:
            configs, fixture, output=self.settings(d)
            def crash(*args,checkpoint,**kwargs):
                checkpoint({'status':'running','audio_seconds':5,'fixture_sha256':'test'})
                raise SystemExit('simulate process exit')
            with patch.object(benchmark,'hardware',return_value={}),patch.object(benchmark,'trial',side_effect=crash):
                with self.assertRaises(SystemExit):benchmark._run_suite(configs,fixture,output,progress=lambda _:None)
            with patch.object(benchmark,'hardware',return_value={}),patch.object(benchmark,'trial') as run:
                report=benchmark._run_suite(configs,fixture,output,progress=lambda _:None)
                run.assert_not_called()
            self.assertEqual(report['trials'][0]['status'],'interrupted')
            self.assertEqual(report['trials'][0]['audio_seconds'],5)

    def test_watchdog_and_realtime_failure(self):
        self.assertIsNone(benchmark.health_failure(0,0,10,10,0))
        self.assertIn('progress',benchmark.health_failure(0,0,21,10,0))
        self.assertIn('backlog',benchmark.health_failure(0,9,10,10,11))
        with tempfile.TemporaryDirectory() as d:
            configs,fixture,output=self.settings(d)
            with patch.object(benchmark,'hardware',return_value={}),patch.object(benchmark,'trial',side_effect=[
                {'status':'ok','headroom_gate':False,'audio_seconds':6,'fixture_sha256':'test'},
                {'status':'ok','realtime_gate':False,'audio_seconds':60,'fixture_sha256':'test'}]) as run:
                report=benchmark._run_suite(configs,fixture,output,progress=lambda _:None)
            self.assertEqual(run.call_count,2);self.assertTrue(report['trials'][-1]['failure_detected'])

if __name__=='__main__':unittest.main()


class InstalledReleaseTests(unittest.TestCase):
    def test_current_version_is_not_offered_again_even_if_sequence_increases(self):
        from unittest.mock import patch
        import feedback
        with tempfile.TemporaryDirectory() as directory:
            client = feedback.Feedback(Path(directory))
            release = {"version": feedback.VERSION, "sequence": feedback.RELEASE_SEQUENCE + 1}
            with patch.object(client, "request", return_value={"payload": "test"}), patch.object(feedback, "verify_manifest", return_value=release):
                self.assertIsNone(client.check_update())
                release["version"] = "99.0.0"
                self.assertEqual(client.check_update(), release)
