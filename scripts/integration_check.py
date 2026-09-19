"""Real offline MLX acceptance check. Models must already exist locally.

Usage: .venv/bin/python scripts/integration_check.py SPEECH_MODEL TRANSLATION_MODEL
Creates a deterministic spoken fixture with macOS say, never records the user.
"""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'Sources/CaptionFlow/Resources/worker'))
import worker
# worker reserves stdout for protocol; this driver reports test results normally.
sys.stdout = worker.PROTOCOL


def main():
    import numpy as np
    from scipy.io import wavfile
    import mlx_whisper
    speech, translation = sys.argv[1:3]
    with tempfile.TemporaryDirectory() as directory:
        fixture = str(Path(directory) / 'speech.wav')
        subprocess.run(['/usr/bin/say', '-v', 'Samantha', '-r', '145', '-o', fixture,
                        '--file-format=WAVE', '--data-format=LEI16@16000',
                        'The train leaves at nine tomorrow morning.'], check=True)
        rate, samples = wavfile.read(fixture)
        assert rate == 16000
        samples = samples.astype(np.float32) / 32768.0
        started = time.monotonic()
        result = mlx_whisper.transcribe(samples, path_or_hf_repo=speech, language='en', verbose=None, temperature=0.0)
        recognized = result['text'].strip()
        assert 'train' in recognized.lower() and 'morning' in recognized.lower(), recognized
        print(json.dumps({'check': 'offline_speech', 'text': recognized, 'seconds': round(time.monotonic() - started, 2)}))

        events = []
        worker.emit = lambda kind, **fields: events.append({'type': kind, **fields})
        translator = worker.PrefixTranslator(translation)
        phrases = ['The train leaves at nine.', 'The train leaves at nine tomorrow morning.', 'The train leaves at ten tomorrow morning.']
        for language in ['French', 'Spanish']:
            for revision, text in enumerate(phrases):
                events.clear()
                translator.translate(text, language, 'en', revision)
                final = events[-1]
                assert final['text'] and final['final']
                if revision > 0:
                    assert final['cached_tokens'] > 0
                # A fresh cache must yield exactly the same greedy translation.
                saved = translator.states.pop(language)
                cached_result = final['text']
                events.clear()
                translator.translate(text, language, 'en', revision)
                assert events[-1]['text'] == cached_result, (cached_result, events[-1]['text'])
                translator.states[language] = saved
                print(json.dumps({'check': 'cached_translation_matches_fresh', 'language': language,
                                  'revision': revision, 'cached_tokens': final['cached_tokens'], 'text': cached_result}, ensure_ascii=False))

        # Exercise the actual JSON-lines bridge, audio inbox, and revision events.
        command = [sys.executable, str(root / 'Sources/CaptionFlow/Resources/worker/worker.py')]
        import base64
        config = {'type': 'configure', 'speech_model': speech, 'translation_model': translation,
                  'targets': ['French'], 'source_language': 'en', 'allow_downloads': False,
                  'cache_dir': directory, 'silence_threshold': 0.008}
        messages = [json.dumps(config)]
        for offset in range(0, len(samples), 3200):
            pcm = samples[offset:offset + 3200].astype('<f4').tobytes()
            messages.append(json.dumps({'type': 'audio', 'pcm': base64.b64encode(pcm).decode()}))
        silence = base64.b64encode(np.zeros(3200, dtype='<f4').tobytes()).decode()
        messages.extend(json.dumps({'type': 'audio', 'pcm': silence}) for _ in range(4))
        messages.append(json.dumps({'type': 'stop'}))
        completed = subprocess.run(command, input='\n'.join(messages) + '\n', text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        assert completed.returncode == 0, completed.stderr[-3000:]
        output = [json.loads(line) for line in completed.stdout.splitlines()]
        assert any(item['type'] == 'ready' for item in output)
        assert any(item['type'] == 'source' and 'train' in item['text'].lower() for item in output), output
        assert any(item['type'] == 'translation' and item['final'] for item in output), output
        assert not any(item['type'] == 'error' for item in output), output
        print(json.dumps({'check': 'worker_protocol_audio_to_translation', 'events': len(output), 'result': 'passed'}))

if __name__ == '__main__': main()
