import base64
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import queue
import time
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
import overlay  # Adds portable local dependencies in development.
import speech
from credentials import save_keys, load_keys


class SpeechTests(unittest.TestCase):
    def test_dpapi_roundtrip_and_no_plaintext(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'credentials.dpapi'
            keys = {'google': 'synthetic-test-secret', 'eleven': ''}
            save_keys(keys, path)
            self.assertNotIn(b'synthetic-test-secret', path.read_bytes())
            self.assertEqual(load_keys(path), {'google': keys['google']})
            save_keys({}, path)
            self.assertEqual(load_keys(path), {})

    def test_google_pcm_and_payload(self):
        result = {'candidates': [{'content': {'parts': [{'inlineData': {
            'mimeType': 'audio/L16;codec=pcm;rate=24000', 'data': base64.b64encode(b'\0'*480).decode()}}]}}]}
        with patch('speech.request', return_value=json.dumps(result).encode()) as request:
            audio = speech.synthesize('google', speech.DEFAULTS['google'], 'test-key', 'Een avontuur.', Path('.'))
        args = request.call_args.args
        self.assertEqual(args[1:3], ('x-goog-api-key', 'test-key'))
        self.assertNotIn('test-key', args[0])
        self.assertEqual(args[3]['generationConfig']['speechConfig']['voiceConfig']['prebuiltVoiceConfig']['voiceName'], 'Charon')
        with wave.open(io.BytesIO(audio)) as stream:
            self.assertEqual(stream.getframerate(),24000); self.assertEqual(stream.getnframes(),240)

    def test_google_empty_response(self):
        with patch('speech.request', return_value=b'{}'):
            with self.assertRaises(speech.SpeechError): speech.synthesize('google',speech.DEFAULTS['google'],'key','text',Path('.'))

    def test_azure_escapes_text_and_restricts_region(self):
        with patch('speech.request',return_value=b'wave') as request:
            speech.synthesize('azure',speech.DEFAULTS['azure'],'key','A < B & C',Path('.'))
        self.assertIn(b'A &lt; B &amp; C',request.call_args.args[3])
        profile = dict(speech.DEFAULTS['azure'],region='evil.example/path')
        with self.assertRaises(speech.SpeechError): speech.synthesize('azure',profile,'key','text',Path('.'))

    def test_http_error_does_not_expose_secret(self):
        import urllib.error
        error=urllib.error.HTTPError('https://example.invalid',401,'sensitive body',{},None)
        with patch('urllib.request.urlopen',side_effect=error):
            with self.assertRaises(speech.SpeechError) as caught: speech.request('https://example.invalid','x-key','secret',{})
        self.assertEqual(str(caught.exception),'API-sleutel ongeldig.')

    def test_stop_during_synthesis_prevents_late_playback(self):
        started, release = threading.Event(), threading.Event()
        def slow(*args):
            started.set(); release.wait(3)
            return speech.wav_bytes(b'\0'*480)
        with tempfile.TemporaryDirectory() as folder, patch('overlay.DATA',Path(folder)), patch('credentials.load_keys',return_value={}), patch('speech.synthesize',side_effect=slow), patch('winsound.PlaySound') as play:
            speaker=speech.Speaker(queue.Queue()); speaker.say('Test.')
            self.assertTrue(started.wait(2)); speaker.stop(); release.set(); time.sleep(.2)
            speaker.closed=True
            self.assertFalse(any(call.args[0] is not None for call in play.call_args_list))

    def test_eleven_payload(self):
        import types
        from array import array
        profile = dict(speech.DEFAULTS['eleven'],voice='voice-id')
        with patch('speech.request',return_value=b'mp3') as request, patch('miniaudio.decode', return_value=types.SimpleNamespace(samples=array('h',[0,0]))):
            audio=speech.synthesize('eleven',profile,'key','Hallo.',Path('.'))
        self.assertTrue(audio.startswith(b'RIFF'))
        self.assertEqual(request.call_args.args[3]['model_id'],'eleven_multilingual_v2')
        self.assertNotIn('language_code',request.call_args.args[3])


if __name__ == '__main__': unittest.main()
