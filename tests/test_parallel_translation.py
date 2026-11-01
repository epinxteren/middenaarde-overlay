import platform_support
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from translation import Translator


class ParallelTranslationTests(unittest.TestCase):
    def test_two_blocks_run_concurrently_and_both_cache_entries_survive(self):
        with tempfile.TemporaryDirectory() as folder:
            translator=object.__new__(Translator)
            translator.language='nl'; translator.glossary={}; translator.cache={}
            translator.cache_lock=threading.Lock(); translator.path=Path(folder)/'translations.json'
            translator.tokenizer=SimpleNamespace(encode=lambda text,**kw:[text])
            barrier=threading.Barrier(2)
            def translate(tokens,**kwargs):
                barrier.wait(timeout=3)
                return [SimpleNamespace(hypotheses=[['Vertaling '+tokens[0][0]]])]
            translator.model=SimpleNamespace(translate_batch=translate)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(translator.translate,['First paragraph.','Second paragraph.']))
            self.assertEqual(len(results),2)
            self.assertEqual(set(json.loads(translator.path.read_text('utf-8'))),{'First paragraph.','Second paragraph.'})
