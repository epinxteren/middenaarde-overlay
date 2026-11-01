import platform_support
import unittest
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
from visualmatch import fingerprint, similarity
from textmatch import canonical_text


class MatchingTests(unittest.TestCase):
    def sample(self, text, offset=0):
        image=Image.new('RGB',(700,65),'#29231b')
        ImageDraw.Draw(image).text((10+offset,10),text,fill='#f0e4bd',font=ImageFont.load_default(size=25))
        return image

    def test_brightness_and_small_shift_reuse_letter_shapes(self):
        first=self.sample('Take 2 cards and continue the adventure.')
        second=ImageEnhance.Brightness(self.sample('Take 2 cards and continue the adventure.',1)).enhance(.9)
        self.assertGreaterEqual(similarity(fingerprint(first),second),.98)

    def test_changed_number_is_not_hidden_by_matching_surroundings(self):
        first=self.sample('Take 2 cards and continue the adventure.')
        second=self.sample('Take 3 cards and continue the adventure.')
        self.assertLess(similarity(fingerprint(first),second),.98)

    def test_ocr_noise_reuses_known_text_but_rule_changes_do_not(self):
        known='The heroes travel through the ancient forest and take 2 cards before they enter the tower.'
        self.assertEqual(canonical_text(known.replace('forest','f0rest'),[known]),known.replace('forest','f0rest'))
        self.assertEqual(canonical_text(known.replace('ancient','anclent'),[known]),known)
        self.assertEqual(canonical_text(known.replace('2 cards','3 cards'),[known]),known.replace('2 cards','3 cards'))
        self.assertEqual(canonical_text(known.replace('before','after'),[known]),known.replace('before','after'))
