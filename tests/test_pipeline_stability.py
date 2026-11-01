"""Exercise the worker across animation, new story pages and empty screens."""
import platform_support
import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from PIL import Image, ImageDraw
from overlay import Block
from qtui import App


class PipelineStabilityTests(unittest.TestCase):
    def test_animation_does_not_cancel_speech_but_new_page_does(self, focus_loss=False):
        images = []
        for page, background in [(1, 0), (1, 80), (1, 150), (2, 150), (2, 0), (0, 0)]:
            image = Image.new('RGB', (800, 600), (background, 0, 0))
            ImageDraw.Draw(image).rectangle((10, 10, 400, 110), fill=(page*100,)*3)
            images.append(image)
        class Clock:
            index = -1
            def is_set(self): return False
            def wait(self, seconds):
                self.index += 1
                return self.index >= len(images)
        clock = Clock()
        calls = []
        class OCR:
            warning, backend = '', 'test'
            async def read(self, image):
                calls.append(clock.index)
                page = image.getpixel((20, 20))[0] // 100
                return ([Block('Story page '+str(page), 10, 10, 390, 100, 20)] if page else [])
        app = object.__new__(App)
        app.closing, app.events = clock, queue.Queue()
        app.enabled = threading.Event(); app.enabled.set()
        app.epoch, app.region, app.min_chars = 0, None, 0
        app.output_language, app.narration_choice = 'nl', 'nl'
        app.ocr_provider, app.ocr_revision = 'portable', 0
        app.child_friendly, app.child_instruction, app.child_pause = False, '', True
        app.detect_enabled, app.detect_minimum = True, 3
        app.create_recognizer = lambda _: OCR()
        model = MagicMock(); model.translate.side_effect = lambda text: 'NL '+text
        # The crude detector fails on every later frame, including a real new
        # page. Once text is tracked, actual OCR must decide if it disappeared.
        def detect(image):
            return SimpleNamespace(lines=5 if clock.index == 0 else 0, layout=(clock.index,))
        with patch('qtui.find_game', return_value=1), patch('qtui.client_box', return_value=(0,0,800,600)), \
                patch('qtui.active_game', side_effect=lambda _: not (focus_loss and clock.index == 1)), \
                patch('qtui.ImageGrab.grab', side_effect=lambda **_: images[clock.index]), \
                patch('qtui.Translator', return_value=model), patch('textdetect.detect_text', side_effect=detect):
            app.worker()
        events = list(app.events.queue)
        self.assertEqual([v[3][0].text for k,v in events if k == 'render'], ['Story page 1', 'Story page 2'])
        self.assertEqual(calls, [0, 0, 3, 3, 5])
        self.assertEqual(sum(k == 'clear' for k,v in events), 3)
        self.assertFalse(any(k == 'detection_empty' for k,v in events))

    def test_focus_return_reuses_page_without_restarting_ocr(self):
        self.test_animation_does_not_cancel_speech_but_new_page_does(focus_loss=True)


if __name__ == '__main__': unittest.main()
