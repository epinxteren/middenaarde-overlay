import platform_support
import unittest
from types import SimpleNamespace
from PIL import Image, ImageDraw
from capturecheck import text_regions_unchanged, text_page_replaced
from translation import normalize_spacing


class CaptureChecks(unittest.TestCase):
    def test_page_switch_requires_strong_change_in_story_region(self):
        before = Image.new('RGB',(400,200),'black')
        block=SimpleNamespace(x=10,y=10,width=300,height=100,text='The heroes explore the ancient forest and search for the hidden passage.')
        after=before.copy()
        ImageDraw.Draw(after).rectangle((320,120,390,190),fill='white')
        self.assertFalse(text_page_replaced(before,after,[block]))
        ImageDraw.Draw(after).rectangle((15,15,19,19),fill='white')
        self.assertFalse(text_page_replaced(before,after,[block]))
        ImageDraw.Draw(after).rectangle((20,20,250,60),fill='white')
        self.assertTrue(text_page_replaced(before,after,[block]))

    def test_background_animation_does_not_discard_text(self):
        before = Image.new('RGB', (100, 100), 'black')
        after = before.copy()
        ImageDraw.Draw(after).rectangle((60, 60, 99, 99), fill='white')
        block = SimpleNamespace(x=10, y=10, width=30, height=20)
        self.assertTrue(text_regions_unchanged(before, after, [block]))
        ImageDraw.Draw(after).rectangle((15, 15, 25, 20), fill='white')
        self.assertFalse(text_regions_unchanged(before, after, [block]))

    def test_offset_and_resized_capture(self):
        image = Image.new('RGB', (100, 100), 'black')
        block = SimpleNamespace(x=210, y=310, width=30, height=20)
        self.assertTrue(text_regions_unchanged(image, image, [block], (200, 300)))
        self.assertFalse(text_regions_unchanged(image, image.resize((90, 90)), [block]))

    def test_sentence_spacing_preserves_decimal_numbers(self):
        self.assertEqual(normalize_spacing('Gevaar.Hier!Kijk?Ja 1.5.'), 'Gevaar. Hier! Kijk? Ja 1.5.')

