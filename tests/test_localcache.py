import platform_support
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from PIL import Image
from overlay import Block
from speech import wav_bytes
from localcache import DiskAudioCache, CachedRecognizer
from translationhelp import AutomaticReviewer


class CacheTests(unittest.TestCase):
    def test_audio_survives_restart_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            data = wav_bytes(b'\0'*480)
            DiskAudioCache(path).put('abc', data)
            self.assertEqual(DiskAudioCache(path).get('abc'), data)
            (path/'abc.wav').write_bytes(b'broken')
            self.assertIsNone(DiskAudioCache(path).get('abc'))

    def test_image_cache_reuses_ocr_and_returns_unmodified_coordinates(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = SimpleNamespace(backend='test', warning='', read=AsyncMock(return_value=[Block('Story',10,20,100,30,20)]))
            image = Image.new('RGB', (200,100))
            first = asyncio.run(CachedRecognizer(engine, Path(folder), 'portable').read(image))
            first[0].x += 300
            second = asyncio.run(CachedRecognizer(engine, Path(folder), 'portable').read(image))
            self.assertEqual(second[0].x, 10)
            self.assertEqual(engine.read.await_count, 1)
            asyncio.run(CachedRecognizer(engine, Path(folder), 'gemini').read(image))
            self.assertEqual(engine.read.await_count, 2)

    def test_ai_cache_survives_restart_and_separates_model_and_language(self):
        with tempfile.TemporaryDirectory() as folder, patch('translationhelp.review') as review:
            result = {'explanation':'', 'items':[{'id':'0','translation':'Moedige helden.', 'narration':'Moedige helden.'}]}
            review.return_value = result
            path = Path(folder)/'ai.json'
            blocks = [Block('Bold heroes.',0,0,100,20,20,'Bold helden.','Bold helden.')]
            AutomaticReviewer(path).improve('key','model',blocks,'nl','nl')
            self.assertEqual(AutomaticReviewer(path).improve('key','model',blocks,'nl','nl'), result)
            self.assertEqual(review.call_count, 1)
            AutomaticReviewer(path).improve('key','model2',blocks,'nl','nl')
            self.assertEqual(review.call_count, 2)
