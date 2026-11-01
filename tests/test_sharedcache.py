import platform_support
import importlib.util
import io
from pathlib import Path
import queue
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import wave
from unittest.mock import patch
from sharedcache import SharedAudioCache, audio_key, server_url, valid_audio

spec = importlib.util.spec_from_file_location('cache_server', Path(__file__).resolve().parents[1]/'cache-server/server.py')
server = importlib.util.module_from_spec(spec); spec.loader.exec_module(server)


def sample():
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\0\0'*2400)
    return out.getvalue()


class SharedCacheTests(unittest.TestCase):
    def test_key_separates_voice_model_language_and_text_not_volume(self):
        profile = {'voice': 'A', 'model': 'one', 'language': 'nl', 'speed': 1, 'volume': 90}
        original = audio_key('google', profile, 'Hallo wereld.')
        self.assertEqual(original, audio_key('google', dict(profile, volume=20), 'Hallo  wereld.'))
        for field, value in [('voice', 'B'), ('model', 'two'), ('language', 'de'), ('speed', 1.2)]:
            self.assertNotEqual(original, audio_key('google', dict(profile, **{field: value}), 'Hallo wereld.'))
        self.assertNotEqual(original, audio_key('eleven', profile, 'Hallo wereld.'))
        self.assertNotEqual(original, audio_key('google', profile, 'Andere tekst.'))

    def test_disabled_or_unconfigured_does_not_access_network(self):
        cache = SharedAudioCache(queue.Queue())
        with patch.object(cache.opener, 'open') as opened:
            self.assertIsNone(cache.get('a'*64))
            cache.configure(False, 'https://example.com', 'token')
            self.assertIsNone(cache.get('a'*64)); cache.put('a'*64, sample())
            opened.assert_not_called()

    def test_url_and_audio_validation(self):
        for value in ['http://example.com', 'https://user:pass@example.com', 'https://example.com?token=secret']:
            with self.assertRaises(ValueError): server_url(value)
        self.assertEqual(server_url('https://example.com/'), 'https://example.com')
        self.assertFalse(valid_audio(b'not audio'))
        self.assertTrue(valid_audio(sample()))

    def test_real_upload_download_and_authentication(self):
        with tempfile.TemporaryDirectory() as folder:
            http = server.make_server(('127.0.0.1', 0), folder, 'x'*32)
            thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
            try:
                url = f'http://127.0.0.1:{http.server_port}'
                cache = SharedAudioCache(queue.Queue()); cache.configure(True, url, 'x'*32)
                key = audio_key('piper', {'voice': 'A'}, 'Hallo')
                self.assertIsNone(cache.get(key))
                cache.put(key, sample()); cache.uploads.join()
                self.assertEqual(cache.get(key), sample())
                for token, body, expected in [('wrong', sample(), 401), ('x'*32, b'invalid', 415)]:
                    req = urllib.request.Request(url+'/v1/audio/'+'b'*64+'.wav', data=body,
                        method='PUT', headers={'Authorization': 'Bearer '+token})
                    with self.assertRaises(urllib.error.HTTPError) as caught: urllib.request.urlopen(req)
                    self.assertEqual(caught.exception.code, expected)
                self.assertEqual(len(list(Path(folder).glob('*.wav'))), 1)
            finally:
                http.shutdown(); http.server_close(); thread.join()
