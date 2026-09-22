import unittest

from PIL import Image, ImageDraw

from scripts.extractors.image_ocr import detect_grid, layout_blocks


class ImageOCRTests(unittest.TestCase):
    def test_grid_and_paragraph_separation(self):
        image = Image.new('RGB', (200, 160), 'white')
        draw = ImageDraw.Draw(image)
        for y in (50, 100, 150):
            draw.line((10, y, 190, y), fill='black', width=2)
        for x in (10, 100, 190):
            draw.line((x, 50, x, 150), fill='black', width=2)
        grid = detect_grid(image)
        self.assertIsNotNone(grid)
        header = 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
        tsv = header + '5\t1\t1\t1\t1\t1\t20\t10\t20\t10\t90\t본문\n'
        tsv += '5\t1\t2\t1\t1\t1\t20\t60\t20\t10\t85\t셀\n'
        blocks = layout_blocks(tsv, grid)
        self.assertEqual(blocks[0]['type'], 'ocr_paragraph')
        self.assertEqual(blocks[1]['rows'][0][0]['text'], '셀')
        self.assertEqual(blocks[1]['rows'][1][1]['text'], '')

    def test_blank_not_table(self):
        self.assertIsNone(detect_grid(Image.new('RGB', (200, 200), 'white')))

    def test_empty_ocr(self):
        self.assertEqual(layout_blocks('level\ttext\n'), [])


if __name__ == '__main__':
    unittest.main()
