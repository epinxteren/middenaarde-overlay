import platform_support
import unittest
from overlay import Block, merge_blocks


class BlockGroupingTests(unittest.TestCase):
    def test_centered_rows_and_mixed_fonts_join_at_exact_pixel_limit(self):
        rows = [Block('Wide heading', 20, 10, 300, 30, 30),
                Block('Centered line', 100, 64, 140, 15, 15),
                Block('Last line', 125, 90, 90, 15, 15)]
        result = merge_blocks(rows, 24)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].text, 'Wide heading Centered line Last line')
        self.assertEqual((result[0].x, result[0].y, result[0].width, result[0].height), (20, 10, 300, 95))
        self.assertEqual(len(merge_blocks(rows, 23)), 2)
        self.assertEqual(rows[0].text, 'Wide heading')

    def test_overlapping_boxes_merge_even_with_zero_distance(self):
        rows = [Block('First', 100, 10, 200, 40, 20),
                Block('Second', 70, 40, 200, 40, 20)]
        result = merge_blocks(rows, 0)
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0].x, result[0].width, result[0].height), (70, 230, 70))

    def test_separate_columns_and_large_gaps_stay_separate(self):
        rows = [Block('Left', 10, 10, 90, 20, 20),
                Block('Right', 200, 10, 90, 20, 20),
                Block('Below', 10, 70, 90, 20, 20)]
        self.assertEqual(len(merge_blocks(rows, 24)), 3)

    def test_intersections_created_by_union_are_resolved(self):
        rows = [Block('Top', 100, 0, 100, 20, 20),
                Block('Side', 0, 30, 95, 10, 10),
                Block('Bottom', 90, 45, 100, 20, 20)]
        result = merge_blocks(rows, 25)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].text, 'Top Side Bottom')


if __name__ == '__main__': unittest.main()
