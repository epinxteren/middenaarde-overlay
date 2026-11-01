import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
import overlay
from PIL import Image
from gemini_ocr import GeminiRecognizer, OCRError, parse_lines, RequestBudget, OCRBudgetExceeded


class OCRTests(unittest.TestCase):
    def test_default_does_not_construct_windows_ocr(self):
        with patch('overlay.WindowsRecognizer', side_effect=AssertionError('Windows must not be used')), patch('portable_ocr.PortableRecognizer'):
            engine = overlay.Recognizer()
            self.assertEqual(engine.backend, 'Ingebouwde OCR')

    def test_missing_windows_pack_falls_back(self):
        with patch('overlay.WindowsRecognizer', side_effect=RuntimeError('Missing language pack')), patch('portable_ocr.PortableRecognizer'):
            engine = overlay.Recognizer('windows')
            self.assertEqual(engine.backend, 'Ingebouwde OCR')
            self.assertIn('niet beschikbaar', engine.warning)

    def test_gemini_coordinates_cache_and_no_key_in_url(self):
        response = {'candidates': [{'content': {'parts': [{'text': json.dumps([
            {'text': 'The old forest.', 'box_2d': [100, 200, 200, 800]}])}]}}]}
        image = Image.new('RGB', (1000, 500))
        with patch('gemini_ocr.request', return_value=json.dumps(response).encode()) as request:
            engine = GeminiRecognizer('synthetic-secret')
            blocks = asyncio.run(engine.read(image))
            self.assertEqual((blocks[0].x, blocks[0].y, blocks[0].width, blocks[0].height), (200, 50, 600, 50))
            blocks[0].x += 300
            cached = asyncio.run(engine.read(image))
            self.assertEqual(cached[0].x, 200)  # Worker offsets must not mutate cached boxes.
            self.assertEqual(request.call_count, 1)
            self.assertNotIn('synthetic-secret', request.call_args.args[0])
            self.assertEqual(request.call_args.args[1], 'x-goog-api-key')
            payload = request.call_args.args[3]
            self.assertEqual(payload['contents'][0]['parts'][1]['inlineData']['mimeType'], 'image/png')

    def test_budget_blocks_without_delays_until_approved_or_minute_expires(self):
        budget = RequestBudget(5)
        with patch('gemini_ocr.time.monotonic', return_value=10), patch('gemini_ocr.time.sleep') as sleep:
            for _ in range(5): budget.consume()
            with self.assertRaises(OCRBudgetExceeded): budget.consume()
            budget.approve()
            for _ in range(5): budget.consume()
            with self.assertRaises(OCRBudgetExceeded): budget.consume()
            sleep.assert_not_called()
        with patch('gemini_ocr.time.monotonic', return_value=71):
            budget.consume()
            self.assertEqual(len(budget.requests), 1)
            self.assertEqual(budget.extra, 0)

    def test_invalid_boxes_are_rejected(self):
        for box in [[0, 0, 1001, 100], [0, 100, 10, 10], [0, 0, float('nan'), 10]]:
            with self.assertRaises(OCRError): parse_lines([{'text': 'text', 'box_2d': box}], 100, 100)

    def test_no_text_is_valid(self):
        self.assertEqual(parse_lines([], 100, 100), [])

    def test_missing_key_never_sends_image(self):
        with patch('gemini_ocr.request') as request:
            with self.assertRaises(OCRError): asyncio.run(GeminiRecognizer().read(Image.new('RGB', (100, 100))))
            request.assert_not_called()

    def test_cancelled_does_not_send_image(self):
        with patch('gemini_ocr.request') as request:
            result = asyncio.run(GeminiRecognizer('key', cancelled=lambda: True).read(Image.new('RGB', (100, 100))))
            self.assertEqual(result, [])
            request.assert_not_called()


if __name__ == '__main__': unittest.main()
