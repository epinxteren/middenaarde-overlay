import platform_support
import unittest
from PIL import Image
from textcrops import pack_text, restore_items


class TextCropTests(unittest.TestCase):
    def test_smaller_payload_and_original_coordinates(self):
        image = Image.new('RGB', (2560, 1440), 'black')
        packed, mapping = pack_text(image, [(100, 200, 700, 230), (100, 250, 700, 280)])
        self.assertIsNotNone(mapping)
        self.assertLess(packed.width*packed.height, image.width*image.height*.2)
        items = []
        for i, (tile, original) in enumerate(mapping):
            x0, y0, x1, y1 = tile
            items.append({'text': str(i), 'box_2d': [y0*1000/packed.height, x0*1000/packed.width,
                         y1*1000/packed.height, x1*1000/packed.width]})
        restored = restore_items(items, packed.size, image.size, mapping)
        for item, (_, original) in zip(restored, mapping):
            y0, x0, y1, x1 = item['box_2d']
            for actual, expected in zip((x0*image.width/1000, y0*image.height/1000,
                                        x1*image.width/1000, y1*image.height/1000), original):
                self.assertAlmostEqual(actual, expected)

    def test_no_candidates_falls_back_without_wrong_coordinates(self):
        image = Image.new('RGB', (200, 200))
        packed, mapping = pack_text(image, [])
        self.assertIs(packed, image); self.assertIsNone(mapping)
