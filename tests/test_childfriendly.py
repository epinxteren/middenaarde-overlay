import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from childfriendly import ChildFriendlyRewriter, ChildFriendlyError, DEFAULT_INSTRUCTION


def response(items):
    return json.dumps({'candidates': [{'content': {'parts': [{'text': json.dumps(items)}]}}]})


class ChildFriendlyTests(unittest.TestCase):
    def setUp(self):
        self.rewriter = ChildFriendlyRewriter('test', 'test-model', DEFAULT_INSTRUCTION)

    def test_deduplicates_and_caches_overlay_and_narration(self):
        original = 'De gevangene wordt gemarteld. Neem 2 kaarten.'
        softened = 'De gevangene wordt slecht behandeld. Neem 2 kaarten.'
        with patch('childfriendly.request', return_value=response([{'id': '0', 'text': softened}])) as request:
            self.assertEqual(self.rewriter.rewrite([original, original]), [softened, softened])
            self.assertEqual(self.rewriter.rewrite([original, '']), [softened, ''])
            request.assert_called_once()
            payload = json.loads(request.call_args.args[3]['contents'][0]['parts'][0]['text'])
            self.assertEqual(len(payload['items']), 1)
            self.assertEqual(payload['instruction'], DEFAULT_INSTRUCTION)

    def test_rejects_changed_rules_without_caching_partial_batch(self):
        with patch('childfriendly.request', return_value=response([
                {'id': '0', 'text': 'Rustige tekst'}, {'id': '1', 'text': 'Neem 3 kaarten.'}])):
            with self.assertRaises(ChildFriendlyError):
                self.rewriter.rewrite(['Enge tekst', 'Neem 2 kaarten.'])
        self.assertEqual(self.rewriter.cache, {})

    def test_rejects_missing_or_invalid_responses(self):
        for items in ([], [{'id': 'wrong', 'text': 'Tekst'}], [{'id': '0', 'text': ''}], [None]):
            with self.subTest(items=items), patch('childfriendly.request', return_value=response(items)):
                with self.assertRaises(ChildFriendlyError): self.rewriter.rewrite(['Verhaal'])

    def test_missing_key_does_not_send_text(self):
        self.rewriter.key = ''
        with patch('childfriendly.request') as request:
            with self.assertRaises(ChildFriendlyError): self.rewriter.rewrite(['Verhaal'])
            request.assert_not_called()
