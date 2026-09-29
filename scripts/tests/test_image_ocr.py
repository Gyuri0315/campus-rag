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

    def test_colspan_is_preserved(self):
        image = Image.new('RGB', (200, 140), 'white')
        draw = ImageDraw.Draw(image)
        for y in (10, 70, 130):
            draw.line((10, y, 190, y), fill='black', width=2)
        draw.line((10, 10, 10, 130), fill='black', width=2)
        draw.line((190, 10, 190, 130), fill='black', width=2)
        draw.line((100, 70, 100, 130), fill='black', width=2)
        grid = detect_grid(image)
        self.assertEqual(grid['cells'][0]['colspan'], 2)
        self.assertEqual(grid['cells'][1]['row'], 1)
        header = 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
        tsv = header + '5\t1\t1\t1\t1\t1\t20\t25\t20\t10\t90\t제목\n'
        tsv += '5\t1\t2\t1\t1\t1\t20\t85\t20\t10\t85\t왼쪽\n'
        tsv += '5\t1\t2\t1\t1\t2\t120\t85\t20\t10\t85\t오른쪽\n'
        table = next(block for block in layout_blocks(tsv, grid) if block['type'] == 'ocr_table')
        self.assertEqual(table['rows'][0][0]['colspan'], 2)
        self.assertEqual(table['rows'][0][0]['text'], '제목')
        self.assertEqual(table['rows'][1][1]['text'], '오른쪽')

    def test_empty_ocr(self):
        self.assertEqual(layout_blocks('level\ttext\n'), [])

    def test_rejects_non_tsv_result(self):
        with self.assertRaisesRegex(ValueError, 'TSV'):
            layout_blocks('plain OCR text')


if __name__ == '__main__':
    unittest.main()
