import platform_support
import json
import unittest
from unittest.mock import patch
from overlay import Block
from translationhelp import review


class TranslationHelpTests(unittest.TestCase):
    def test_sends_original_and_game_context_and_corrects_deduction(self):
        block = Block('Use deduction to win.', 0, 0, 200, 30, 20, 'Gebruik aftrek om te winnen.', 'Gebruik aftrek om te winnen.')
        result = {'explanation': 'Deduction betekent hier logisch redeneren.', 'items': [
            {'id': '0', 'translation': 'Gebruik logisch redeneren om te winnen.', 'narration': 'Gebruik logisch redeneren om te winnen.'}]}
        raw = json.dumps({'candidates': [{'content': {'parts': [{'text': json.dumps(result)}]}}]})
        with patch('translationhelp.request', return_value=raw) as request:
            actual = review('test', 'test-model', [block], 'Verbeter aftrek', 'nl', 'nl')
        self.assertEqual(actual, result)
        payload = request.call_args.args[3]
        self.assertIn('Journeys in Middle-earth', payload['systemInstruction']['parts'][0]['text'])
        context = json.loads(payload['contents'][0]['parts'][0]['text'])
        self.assertEqual(context['items'][0]['english'], block.text)

    def test_rejects_changed_numbers(self):
        block = Block('Take 2 cards.', 0, 0, 200, 30, 20, 'Neem 2 kaarten.', 'Neem 2 kaarten.')
        result = {'explanation': '', 'items': [{'id': '0', 'translation': 'Neem 3 kaarten.', 'narration': 'Neem 3 kaarten.'}]}
        raw = json.dumps({'candidates': [{'content': {'parts': [{'text': json.dumps(result)}]}}]})
        with patch('translationhelp.request', return_value=raw):
            with self.assertRaises(ValueError): review('test', 'test-model', [block], 'Verbeter', 'nl', 'nl')
