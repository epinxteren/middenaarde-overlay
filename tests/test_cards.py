import platform_support
import asyncio
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import numpy as np
from cards import CardLibrary, CaptureGate, card_crop, text_key, readable_text
from cardui import camera_scan
from overlay import Block


class CardTests(unittest.TestCase):
    def test_library_survives_restart_and_keeps_rule_variants(self):
        with tempfile.TemporaryDirectory() as folder:
            library = CardLibrary(Path(folder))
            first = library.save('Attack 2.\nMove 3.', b'photo')
            self.assertEqual(first, library.save('Attack 2. Move 3.', b'other photo'))
            other = library.save('Attack 3. Move 3.', b'photo')
            self.assertNotEqual(first, other)
            library.reading(first, {'language': 'nl', 'child': False}, 'Aanval 2. Beweeg 3.')
            fresh = CardLibrary(Path(folder))
            self.assertEqual(len(fresh.entries()), 2)
            self.assertEqual(fresh.reading(first, {'child': False, 'language': 'nl'}), 'Aanval 2. Beweeg 3.')
            self.assertIsNone(fresh.reading(first, {'language': 'en', 'child': False}))
            self.assertIsNone(fresh.reading(first, {'language': 'nl', 'child': True}))

    def test_quality_gate_requires_stability_and_rejects_blank(self):
        gate = CaptureGate()
        sharp = np.random.default_rng(7).integers(30, 220, (400, 280, 3), dtype=np.uint8)
        self.assertFalse(gate.ready(sharp, 0))
        self.assertFalse(gate.ready(sharp, .4))
        self.assertTrue(gate.ready(sharp, .9))
        self.assertFalse(gate.ready(np.zeros_like(sharp), 1))
        self.assertFalse(gate.ready(np.zeros_like(sharp), 5))

    def test_crop_stays_inside_camera_image(self):
        for width, height in [(1920, 1080), (320, 240), (200, 400)]:
            for landscape in [False, True]:
                crop, (x, y, w, h) = card_crop(np.zeros((height,width,3),dtype=np.uint8), landscape)
                self.assertEqual(crop.shape[:2], (h,w))
                self.assertGreaterEqual(x,0); self.assertGreaterEqual(y,0)
                self.assertLessEqual(x+w,width); self.assertLessEqual(y+h,height)

    def test_two_consistent_recognitions_capture_once_and_release_camera(self):
        camera = MagicMock()
        camera.read.return_value = (True, np.full((480,640,3), 100, np.uint8))
        block = Block('Attack 2. Move 3.', 0,0,100,50,20)
        recognizer = MagicMock(); recognizer.read = AsyncMock(return_value=[block])
        events, previews = queue.Queue(), queue.Queue(1)
        with patch('cv2.VideoCapture', return_value=camera), patch('portable_ocr.PortableRecognizer', return_value=recognizer), patch('cards.CaptureGate.ready', return_value=True):
            camera_scan(0, False, threading.Event(), events, previews)
        self.assertEqual(recognizer.read.call_count, 2)
        captures = []
        while not events.empty():
            item = events.get()
            if item[0] == 'capture': captures.append(item)
        self.assertEqual(len(captures),1)
        self.assertEqual(captures[0][1][0], block.text)
        self.assertTrue(captures[0][1][1].startswith(b'\xff\xd8'))
        camera.release.assert_called_once()

    def test_unavailable_camera_reports_error_and_releases(self):
        camera = MagicMock(); camera.isOpened.return_value = False
        events = queue.Queue()
        with patch('cv2.VideoCapture', return_value=camera):
            camera_scan(0,False,threading.Event(),events,queue.Queue(1))
        self.assertEqual(events.get()[0], 'error')
        camera.release.assert_called_once()

    def test_cancelled_scan_never_emits_capture(self):
        camera = MagicMock(); cancelled = threading.Event(); cancelled.set()
        events = queue.Queue()
        with patch('cv2.VideoCapture', return_value=camera), patch('portable_ocr.PortableRecognizer'):
            camera_scan(0,False,cancelled,events,queue.Queue(1))
        self.assertTrue(events.empty()); camera.release.assert_called_once()


if __name__ == '__main__': unittest.main()
