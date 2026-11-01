import platform_support
import unittest
from PIL import Image, ImageDraw, ImageFont
from textdetect import detect_text


class TextDetectionTests(unittest.TestCase):
    def test_text_inside_closed_story_panel_is_detected(self):
        image = Image.new('RGB', (1000,480), '#231f19')
        draw = ImageDraw.Draw(image)
        draw.rectangle((30,30,970,360), fill='#eee1bb')
        font = ImageFont.load_default(size=27)
        for i,line in enumerate(['The heroes travel through the forest.',
                                 'An enemy appears near the old bridge.',
                                 'The brave heroes must protect the village.']):
            draw.text((65,70+i*65),line,font=font,fill='#181818')
        self.assertGreaterEqual(detect_text(image).lines, 3)

    def test_blank_image_has_no_text(self):
        self.assertEqual(detect_text(Image.new('RGB', (900, 500), 'black')).lines, 0)

    def test_paragraph_is_detected_locally(self):
        image = Image.new('RGB', (900, 500), 'white')
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=24)
        for index, line in enumerate(['The heroes travel through the forest.',
                'They discover an ancient tower near the river.',
                'Take two cards and continue the adventure.',
                'A mysterious stranger waits by the old bridge.']):
            draw.text((30, 40+index*45), line, font=font, fill='black')
        result = detect_text(image)
        self.assertGreaterEqual(result.lines, 3)
        self.assertEqual(result, detect_text(image))
