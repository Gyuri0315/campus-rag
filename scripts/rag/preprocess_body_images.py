"""Target IMAGE_ONLY_REQUIRES_OCR documents without modifying crawl state.

python -m scripts.rag.preprocess_body_images --dataset ce
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from bs4 import BeautifulSoup
import requests

from scripts.crawlers.departments.body_images import collect_body_images, save_body_images
from scripts.extractors.image_ocr import VERSION, extract_image

ROOT = Path(__file__).resolve().parents[2]


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.part')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', action='append', help='Repeatable; default: all datasets')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--tesseract', default=shutil.which('tesseract') or 'C:/Program Files/Tesseract-OCR/tesseract.exe')
    parser.add_argument('--tessdata')
    parser.add_argument('--language', default='kor+eng')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    command = [args.tesseract, '--list-langs']
    if args.tessdata:
        command += ['--tessdata-dir', args.tessdata]
    try:
        installed = subprocess.run(command, capture_output=True, text=True, check=True).stdout.splitlines()
        missing = set(args.language.split('+')) - set(installed)
        if missing:
            parser.error('Missing Tesseract languages: ' + ', '.join(sorted(missing)))
    except (OSError, subprocess.CalledProcessError) as exc:
        parser.error(f'Tesseract unavailable: {exc}')
    datasets = args.dataset or [p.name for p in (ROOT/'files').iterdir() if (p/'output/json').is_dir()]
    report = []
    with requests.Session() as session:
        for dataset in datasets:
            if Path(dataset).name != dataset or dataset in {'.', '..'}:
                parser.error('Invalid dataset')
            output = ROOT/'files'/dataset/'output'
            for path in sorted((output/'json').rglob('*.json')):
                doc = json.loads(path.read_text(encoding='utf-8'))
                if 'IMAGE_ONLY_REQUIRES_OCR' not in doc.get('crawl', {}).get('warnings', []):
                    continue
                if args.limit is not None and len(report) >= args.limit:
                    break
                target = ROOT/'files'/dataset/'preprocessed/body_images'/path.relative_to(output/'json')
                entry = {'source_path': path.relative_to(ROOT).as_posix(), 'url': doc.get('url'), 'images': [],
                         'processed_at': datetime.now(timezone.utc).isoformat(), 'ocr_version': VERSION,
                         'language': args.language, 'status': 'needs_review'}
                try:
                    html = (output/'html'/path.relative_to(output/'json')).with_suffix('.html')
                    records = doc.get('metadata', {}).get('body_images', [])
                    if not records:
                        candidates = collect_body_images(BeautifulSoup(html.read_text(encoding='utf-8'), 'html.parser'))
                        records = save_body_images(candidates, session=session, page_url=doc['url'],
                                                   output_dir=output/'images', project_root=ROOT)
                    old = json.loads(target.read_text(encoding='utf-8')) if target.exists() else {}
                    cached = {r.get('sha256'): r for r in old.get('images', []) if r.get('blocks')}
                    for record in records:
                        record = dict(record)
                        try:
                            if record.get('status') != 'saved':
                                raise ValueError(str(record.get('error') or 'Image not saved'))
                            image_path = (ROOT/record['saved_path']).resolve()
                            image_path.relative_to(output.resolve())
                            digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
                            if digest != record['sha256']:
                                raise ValueError('Image hash mismatch')
                            if not args.force and old.get('ocr_version') == VERSION and old.get('language') == args.language and digest in cached:
                                record = cached[digest]
                            else:
                                record.update(extract_image(image_path, executable=args.tesseract,
                                                            language=args.language, tessdata=args.tessdata))
                        except Exception as exc:
                            record.update(status='failed', ocr_error=str(exc))
                        entry['images'].append(record)
                    if not records or any(r['status'] in {'failed', 'empty'} for r in entry['images']):
                        entry['status'] = 'partial_failure' if any(r.get('blocks') for r in entry['images']) else 'failed'
                except Exception as exc:
                    entry.update(status='failed', error=str(exc))
                write_json(target, entry)
                report.append({'source_path': entry['source_path'], 'status': entry['status'],
                               'images': len(entry['images']),
                               'blocks': sum(len(r.get('blocks', [])) for r in entry['images'])})
                print(json.dumps(report[-1], ensure_ascii=False), flush=True)
    write_json(ROOT/'files/_reviewed/body_image_ocr/latest.json', report)


if __name__ == '__main__':
    main()
