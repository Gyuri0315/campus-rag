"""Retry only failed attachments; never reset crawl state or download successes."""
import argparse
import json
from pathlib import Path

from scripts.crawlers.departments import engine as e
from scripts.crawlers.departments.config import load_registry
from scripts.crawlers.departments.attachments import attachment_candidate
from scripts.crawlers.common.schema import RunResult


def retry(dataset, limit=20, error_code=None, document=None):
    e.configure_department(load_registry()[dataset])
    result = RunResult(dataset=dataset, mode='incremental')
    e.set_run_id(e.log_context, result.run_id)
    session = e.build_session()
    attempted = 0
    details = []
    paths = [Path(document)] if document else e.PATHS.json.rglob('*.json')
    for path in paths:
        path = path.resolve()
        if not path.is_relative_to(e.PATHS.json.resolve()):
            raise ValueError('document must belong to the selected dataset')
        doc = json.loads(path.read_text(encoding='utf-8'))
        failed = [a for a in doc.get('attachments', []) if a.get('error') and not a.get('downloaded')
                  and a['error']['code'] != 'NOT_ATTACHMENT'
                  and (not error_code or a['error']['code'] == error_code)]
        if not failed:
            continue
        # Open the original public page normally to establish cookies and Referer.
        page = None
        outcomes = {a['url']: a for a in doc['attachments'] if a.get('downloaded')}
        changed = False
        for index, old in enumerate(doc['attachments']):
            if old not in failed:
                continue
            if old['url'] in outcomes:
                doc['attachments'][index] = {**outcomes[old['url']], 'id': old['id']}
                changed = True
                continue
            if attempted >= limit:
                break
            attempted += 1
            if not attachment_candidate(old['url'], old.get('name', '')):
                new = {**old, 'error': e.attachment_error('NOT_ATTACHMENT', 'Link lacks file or download endpoint evidence', False)}
            else:
                if page is None:
                    page = e.fetch(session, doc['url'])
                if page is None:
                    result.add_error(e._LAST_FETCH_FAILURE.code, e._LAST_FETCH_FAILURE.message,
                                     url=doc['url'], retryable=e._LAST_FETCH_FAILURE.retryable)
                    details.append({'document': str(path), 'url': old['url'], 'before': old['error']['code'],
                                    'after': e._LAST_FETCH_FAILURE.code, 'stage': 'source_page'})
                    continue
                new = e.save_attachments(session, [old], doc['category'], doc['slug'], page.url)[0]
            new['id'] = old['id']
            outcomes[old['url']] = new
            doc['attachments'][index] = new
            changed = True
            result.stats.count_attachments([new])
            e.log_attachment_events(e.log, [new], source_id=doc.get('source_id'))
            details.append({'document': str(path), 'url': old['url'], 'before': old['error']['code'],
                            'after': (new.get('error') or {}).get('code', 'downloaded')})
        if changed:
            temp = path.with_suffix('.retry.tmp')
            temp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(path)
        if attempted >= limit:
            break
    result.finish()
    receipt = result.save(e.PROJECT_ROOT)
    detail_dir = receipt.parent / 'details'
    detail_dir.mkdir(exist_ok=True)
    (detail_dir / receipt.name).write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'result': str(receipt), 'stats': result.stats.to_dict(), 'details': details}, ensure_ascii=False))
    return result.exit_code


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset', required=True)
    p.add_argument('--limit', type=int, default=20)
    p.add_argument('--error-code')
    p.add_argument('--document', type=Path)
    args = p.parse_args()
    if args.limit < 1:
        p.error('--limit must be positive')
    return retry(args.dataset, args.limit, args.error_code, args.document)


if __name__ == '__main__':
    raise SystemExit(main())
