"""Tesseract TSV layout extraction; conservative, fully ruled tables only."""
from __future__ import annotations

import csv
import io
import subprocess
from collections import defaultdict

import numpy as np
from PIL import Image, ImageOps

VERSION = '1'


def _centers(indices):
    groups = []
    for value in indices:
        if not groups or value > groups[-1][-1] + 1:
            groups.append([])
        groups[-1].append(int(value))
    return [round(sum(group) / len(group)) for group in groups]


def detect_grid(image):
    """Only accept continuous horizontal/vertical ruling, not guessed columns."""
    dark = np.asarray(image.convert('L')) < 150
    ys = _centers(np.flatnonzero(dark.mean(axis=1) > .65))
    if len(ys) < 3:
        return None
    xs = _centers(np.flatnonzero(dark[ys[0]:ys[-1] + 1].mean(axis=0) > .85))
    if len(xs) < 3 or min(np.diff(xs)) < 12 or min(np.diff(ys)) < 12:
        return None
    # Reject partial/merged ruling: assigning a guessed span would corrupt meaning.
    for y in ys:
        if dark[max(0, y-2):y+3, xs[0]:xs[-1]+1].any(axis=0).mean() < .95:
            return None
    return xs, ys


def layout_blocks(tsv, grid=None):
    lines = defaultdict(list)
    cells = defaultdict(list)
    for word in csv.DictReader(io.StringIO(tsv), delimiter='\t', quoting=csv.QUOTE_NONE):
        if word['level'] != '5' or not word['text'].strip():
            continue
        x, y, w, h = (int(word[key]) for key in ('left', 'top', 'width', 'height'))
        item = {'text': word['text'], 'bbox': [x, y, x+w, y+h], 'confidence': float(word['conf'])}
        if grid:
            xs, ys = grid
            cx, cy = x+w/2, y+h/2
            col, row = np.searchsorted(xs, cx)-1, np.searchsorted(ys, cy)-1
            if 0 <= col < len(xs)-1 and 0 <= row < len(ys)-1:
                cells[int(row), int(col)].append(item)
                continue
        lines[tuple(word[k] for k in ('page_num', 'block_num', 'par_num'))].append(item)
    blocks = []
    for words in lines.values():
        boxes = [word['bbox'] for word in words]
        blocks.append({'type': 'ocr_paragraph', 'text': ' '.join(w['text'] for w in words),
                       'bbox': [min(b[0] for b in boxes), min(b[1] for b in boxes),
                                max(b[2] for b in boxes), max(b[3] for b in boxes)],
                       'confidence': sum(w['confidence'] for w in words)/len(words)})
    if grid and cells:
        xs, ys = grid
        rows = []
        for row in range(len(ys)-1):
            rows.append([{'row': row, 'column': col, 'rowspan': 1, 'colspan': 1,
                          'text': ' '.join(w['text'] for w in cells[row, col]),
                          'bbox': [xs[col], ys[row], xs[col+1], ys[row+1]]}
                         for col in range(len(xs)-1)])
        blocks.append({'type': 'ocr_table', 'rows': rows,
                       'bbox': [xs[0], ys[0], xs[-1], ys[-1]],
                       'text': '\n'.join(' | '.join(c['text'] for c in row) for row in rows)})
    return sorted(blocks, key=lambda b: (b['bbox'][1], b['bbox'][0]))


def extract_image(path, *, executable, language='kor+eng', tessdata=None):
    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original).convert('RGB')
        grid = detect_grid(image)
        # Send exactly the oriented raster used by grid detection to Tesseract.
        data = io.BytesIO()
        image.save(data, format='PNG')
    command = [str(executable), 'stdin', 'stdout', '-l', language, '--psm', '3']
    if tessdata:
        command += ['--tessdata-dir', str(tessdata)]
    result = subprocess.run(command + ['tsv'], input=data.getvalue(), capture_output=True,
                            timeout=180, check=True)
    blocks = layout_blocks(result.stdout.decode('utf-8-sig'), grid)
    return {'blocks': blocks, 'status': 'needs_review' if blocks else 'empty',
            'warnings': ['OCR_REVIEW_REQUIRED'] + ([] if grid else ['TABLE_LAYOUT_UNVERIFIED']),
            'width': image.width, 'height': image.height}
