import platform_support
import unittest
from portable_ocr import restore_spaces


class SpacingTests(unittest.TestCase):
    def test_missing_spaces(self):
        self.assertEqual(restore_spaces('growingcloser.', 'growing closer.'), 'growing closer.')
        self.assertEqual(restore_spaces('suchacreaturetobesummonedhere.',
            'such a creature to be summoned here.'), 'such a creature to be summoned here.')

    def test_no_letter_or_punctuation_changes(self):
        for original, candidate in [('Mirkwood', 'Mirk wood.'), ('thirteen', 'three ten'),
                                    ('Tracks needed: 3.', 'Tracks needed: 8.')]:
            self.assertEqual(restore_spaces(original, candidate), original)

    def test_existing_boundaries_preserved(self):
        self.assertEqual(restore_spaces('the enemy', 'theenemy'), 'the enemy')
        self.assertEqual(restore_spaces('a bcdefghijkl', 'ab cdefghijkl'), 'a bcdefghijkl')


if __name__ == '__main__': unittest.main()
