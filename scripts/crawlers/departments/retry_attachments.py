"""Recover failed attachments without recrawling boards or replacing successes."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from scripts.crawlers.departments import engine as e
from scripts.crawlers.departments.config import load_registry
from scripts.crawlers.departments.attachments import attachment_candidate, board_reference
from scripts.crawlers.common.schema import RunResult
from scripts.crawlers.common.storage import get_dataset_paths

RECOVERABLE_ERRORS = frozenset({
    'TLS_ERROR', 'REQUEST_FAILED', 'STREAM_FAILED', 'UNSUPPORTED_CONTENT_TYPE',
    'UNSUPPORTED_FILE_TYPE', 'HTTP_429', 'HTTP_500', 'HTTP_502', 'HTTP_503', 'HTTP_504',
    'RESOLVED_REFERENCE',
})


def _codes(value):
    if value is None:
        return RECOVERABLE_ERRORS
    return {value} if isinstance(value, str) else set(value)


def _reference(old, *, status='not_attachment'):
    return {
        'url': old['url'], 'label': old.get('name', ''),
        'source': 'reclassified_attachment', 'followed': False, 'status': status,
        'source_attachment_id': old['id'], 'attachment_metadata': deepcopy(old),
    }


def _restore_attachment_references(doc, selected_codes):
    """Reconsider a preserved link after support for its download endpoint is added."""
    remaining = []
    used_ids = {a.get('id') for a in doc.get('attachments', [])}
    for reference in doc.get('reference_links', []):
        old = reference.get('attachment_metadata') or {}
        if (reference.get('source') == 'reclassified_attachment'
                and (old.get('error') or {}).get('code') in selected_codes
                and old.get('id') not in used_ids
                and attachment_candidate(old.get('url', ''), old.get('name', ''))):
            doc.setdefault('attachments', []).append(deepcopy(old))
            used_ids.add(old['id'])
        else:
            remaining.append(reference)
    if 'reference_links' in doc:
        doc['reference_links'] = remaining


def _resolve_reference(session, old, output_dir):
    """Inspect at most one linked public CMS post; never execute redirect scripts."""
    reference = _reference(old)
    parts = urlsplit(old['url'])
    if not (board_reference(old['url']) and parts.hostname == 'cms.pknu.ac.kr'):
        return [], reference
    response = e.fetch(session, old['url'])
    reference['followed'] = True
    if response is None:
        failure = e._LAST_FETCH_FAILURE
        reference.update(status='deferred', error={
            'code': failure.code if failure else 'SOURCE_PAGE_FAILED',
            'message': failure.message if failure else 'Public reference unavailable',
        })
        return [], reference
    try:
        raw = response.content
        key = hashlib.sha256(old['url'].encode()).hexdigest()[:20]
        snapshot = output_dir / (key + '.html')
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(raw)
        reference['snapshot'] = {
            'saved_path': snapshot.relative_to(e.PROJECT_ROOT).as_posix(),
            'size_bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
        }
        reference['final_url'] = response.url
        soup = BeautifulSoup(response.text, 'lxml')
        content = soup.select_one('.a_bdCont, .c_bdCont, .bdCont, form#frmPost')
        candidates = e.get_adapter('numeric_cms').parse_attachments(
            content, page_url=response.url, base_url=e.BASE_URL,
            site_prefix=e.ACTIVE_CONFIG.site_prefix,
        ) if content is not None else []
        reference['status'] = 'resolved_files' if candidates else 'no_file_links'
        reference['discovered_files'] = len(candidates)
        reference['discovered_attachments'] = candidates
        # Bounded to attachments in one explicit detail container.
        return candidates[:20], reference
    finally:
        response.close()


def _refresh_status(doc):
    crawl = doc.get('crawl')
    if not isinstance(crawl, dict):
        return
    warnings = [w for w in crawl.get('warnings', [])
                if w not in {'ATTACHMENT_FAILURES', 'ATTACHMENT_DOWNLOAD_FAILED'}]
    if any(a.get('error') for a in doc.get('attachments', [])):
        warnings.append('ATTACHMENT_DOWNLOAD_FAILED')
    crawl['warnings'] = list(dict.fromkeys(warnings))
    if crawl.get('status') != 'failed':
        crawl['status'] = 'partial_success' if any(w != 'ATTACHMENT_ONLY' for w in warnings) else 'success'


def retry(dataset, limit=20, error_code=None, document=None, *, dry_run=False,
          output_root=None, summary=None, document_paths=None):
    config = load_registry()[dataset]
    paths = get_dataset_paths(e.PROJECT_ROOT, dataset, group='department')
    candidates = ([Path(document)] if document else document_paths
                  if document_paths is not None else paths.json.rglob('*.json'))
    selected_codes = _codes(error_code)
    jobs = []
    for path in candidates:
        path = path.resolve()
        if not path.is_relative_to(paths.json.resolve()):
            raise ValueError('document must belong to the selected dataset')
        raw = path.read_bytes()
        doc = json.loads(raw.decode('utf-8-sig'))
        _restore_attachment_references(doc, selected_codes)
        if any(a.get('error') and not a.get('downloaded')
               and a['error']['code'] in selected_codes for a in doc.get('attachments', [])):
            jobs.append((path, raw, doc))
    if not jobs:
        return 0
    if dry_run:
        count = sum(1 for _, _, doc in jobs for a in doc.get('attachments', [])
                    if a.get('error') and not a.get('downloaded')
                    and a['error']['code'] in selected_codes)
        if summary is not None:
            summary.update(dataset=dataset, documents=len(jobs), selected_attachments=count)
        print(json.dumps({'dataset': dataset, 'documents': len(jobs),
                          'selected_attachments': count, 'dry_run': True}), flush=True)
        return 0

    e.configure_department(config)
    result = RunResult(dataset=dataset, mode='attachment_retry')
    result.run_id += '-attachments'
    e.set_run_id(e.log_context, result.run_id)
    session = e.build_session()
    folder = (Path(output_root) / dataset if output_root
              else paths.runs / 'attachment_retries' / result.run_id).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    receipt = folder / 'result.json'
    details = []
    attempted = 0
    recovered = 0
    references = 0
    cache = {}
    # A repeated URL is requested once per dataset, including terminal failures.
    reference_cache = {}

    def checkpoint():
        payload = result.to_dict()
        payload.update(scope='attachment_retry', attempted=attempted, recovered=recovered,
                       reclassified_references=references, details=details)
        e.save_json_atomic(receipt, payload)
        return payload

    try:
        for path, original_bytes, doc in jobs:
            backup = folder / 'documents_before' / path.relative_to(paths.json.resolve())
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                backup.write_bytes(original_bytes)
            page = None
            page_checked = False
            changed = False
            keep = []
            extra = []
            for good in doc['attachments']:
                saved_path = good.get('saved_path')
                if good.get('downloaded') and saved_path and (e.PROJECT_ROOT / saved_path).is_file():
                    cache.setdefault(good['url'], good)
            next_id = max((int(a['id'].rsplit('-', 1)[-1]) for a in doc['attachments']), default=0) + 1
            for old in doc['attachments']:
                if (old.get('downloaded') or not old.get('error')
                        or old['error']['code'] not in selected_codes or attempted >= limit):
                    keep.append(old)
                    continue
                attempted += 1
                result.stats.requested += 1
                before = old['error']['code']
                if not attachment_candidate(old['url'], old.get('name', '')):
                    if old['url'] not in reference_cache:
                        reference_cache[old['url']] = _resolve_reference(
                            session, old, paths.html / 'attachment_references')
                    files, reference = deepcopy(reference_cache[old['url']])
                    reference['source_attachment_id'] = old['id']
                    reference['attachment_metadata'] = deepcopy(old)
                    refs = doc.setdefault('reference_links', [])
                    refs[:] = [r for r in refs if not (
                        r.get('url') == old['url'] and r.get('source') == 'reclassified_attachment')]
                    refs.append(reference)
                    references += 1
                    if board_reference(old['url']) and not files:
                        result.add_error('REFERENCE_FILE_UNAVAILABLE', reference['status'],
                                         url=old['url'], source_id=doc.get('source_id'), retryable=False)
                    changed = True
                    for index, file in enumerate(files):
                        ident = old['id'] if index == 0 else f'attachment-{next_id:03d}'
                        if index:
                            next_id += 1
                        extra.append({**file, 'id': ident, 'downloaded': False,
                                      'error': {'code': 'RESOLVED_REFERENCE', 'message': '', 'retryable': True},
                                      'source_reference_url': old['url']})
                    details.append({'document': str(path), 'url': old['url'], 'before': before,
                                    'after': 'reference_link', 'reference_status': reference['status']})
                    print(f'[{dataset}] reference {attempted}: {reference["status"]}', flush=True)
                    continue
                if old['url'] in cache:
                    new = {**deepcopy(old), **deepcopy(cache[old['url']]), 'id': old['id']}
                else:
                    if not page_checked:
                        page = e.fetch(session, doc['url'])
                        page_checked = True
                    if page is None:
                        failure = e._LAST_FETCH_FAILURE
                        new = deepcopy(old)
                        new['recovery'] = {'status': 'deferred', 'at': e.now_kst(),
                                           'code': failure.code if failure else 'SOURCE_PAGE_FAILED'}
                    else:
                        new = e.save_attachments(session, [old], doc['category'], doc['slug'], page.url)[0]
                        new['id'] = old['id']
                        new['recovery'] = {'at': e.now_kst(), 'previous_error': old['error']}
                    cache[old['url']] = deepcopy(new)
                recovered += int(new.get('downloaded') is True)
                result.stats.count_attachments([new])
                e.log_attachment_events(e.log, [new], source_id=doc.get('source_id'))
                keep.append(new)
                changed = True
                after = (new.get('error') or {}).get('code', 'downloaded')
                details.append({'document': str(path), 'url': old['url'], 'before': before, 'after': after})
                print(f'[{dataset}] attachment {attempted}: {before} -> {after}', flush=True)
            # New files are discovered only from one explicitly linked public CMS post.
            for file in extra:
                if attempted >= limit:
                    keep.append(file)
                    continue
                attempted += 1
                result.stats.requested += 1
                new = e.save_attachments(session, [file], doc['category'], doc['slug'],
                                         file['source_reference_url'])[0]
                new['id'] = file['id']
                keep.append(new)
                recovered += int(new.get('downloaded') is True)
                result.stats.count_attachments([new])
                e.log_attachment_events(e.log, [new], source_id=doc.get('source_id'))
            if changed:
                doc['attachments'] = keep
                _refresh_status(doc)
                # Do not overwrite a concurrently edited or recrawled document.
                if path.read_bytes() != original_bytes:
                    raise RuntimeError(f'Document changed during recovery: {path}')
                e.save_json_atomic(path, doc)
            checkpoint()
            if attempted >= limit:
                break
    except KeyboardInterrupt:
        result.finish('cancelled')
        checkpoint()
        raise
    except Exception as exc:
        result.add_error('ATTACHMENT_RECOVERY_FAILED', str(exc), retryable=False)
        result.finish('failed')
        checkpoint()
        raise
    finally:
        session.close()
    result.finish()
    payload = checkpoint()
    if summary is not None:
        summary.update(dataset=dataset, result=str(receipt), status=result.status,
                       attempted=attempted, recovered=recovered,
                       reclassified_references=references, stats=result.stats.to_dict())
    print(json.dumps({'result': str(receipt), 'stats': result.stats.to_dict(),
                      'recovered': recovered, 'references': references}), flush=True)
    return result.exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--dataset')
    selection.add_argument('--all', action='store_true')
    parser.add_argument('--limit', type=int, default=20, help='Per-dataset attachment limit')
    parser.add_argument('--error-code', action='append', help='Repeat for multiple failure codes')
    parser.add_argument('--document', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--output-root', type=Path)
    parser.add_argument('--failure-report', type=Path,
                        help='Select document/source IDs from a reviewed attachment failure report')
    args = parser.parse_args()
    if args.limit < 1:
        parser.error('--limit must be positive')
    if args.all and args.document:
        parser.error('--document requires --dataset')
    registry = load_registry()
    targets = [args.dataset] if args.dataset else [name for name, cfg in registry.items() if cfg.crawl_ready]
    indexed = None
    missing_sources = []
    if args.failure_report:
        failure_report = json.loads(args.failure_report.read_text(encoding='utf-8-sig'))
        indexed = {}
        seen = set()
        for item in failure_report['events']:
            dataset = item['dataset']
            if dataset not in targets or item['code'] not in _codes(args.error_code):
                continue
            key = (dataset, str(item['source_id']))
            if key in seen:
                continue
            seen.add(key)
            paths = get_dataset_paths(e.PROJECT_ROOT, dataset, group='department')
            slug = e.document_slug(dataset, item['source_id'])
            matches = list(paths.json.glob(f'*/{slug}.json')) + list(paths.json.glob(f'{slug}.json'))
            if not matches:
                missing_sources.append({'dataset': dataset, 'source_id': item['source_id']})
            indexed.setdefault(dataset, []).extend(matches)
        targets = [name for name in targets if name in indexed]
    summaries = []
    exit_code = 0
    for dataset in targets:
        summary = {}
        code = retry(dataset, args.limit, args.error_code, args.document,
                     dry_run=args.dry_run, output_root=args.output_root, summary=summary,
                     document_paths=indexed[dataset] if indexed is not None else None)
        if summary:
            summaries.append(summary)
        exit_code = max(exit_code, code)
        if args.output_root and not args.dry_run:
            e.save_json_atomic(args.output_root / 'batch.json',
                               {'scope': 'attachment_retry', 'status': 'running', 'results': summaries,
                                'missing_sources': missing_sources})
    if args.output_root and not args.dry_run:
        e.save_json_atomic(args.output_root / 'batch.json',
                           {'scope': 'attachment_retry', 'status': 'completed',
                            'finished_at': e.now_kst(), 'results': summaries,
                            'missing_sources': missing_sources})
    if args.dry_run:
        print(json.dumps({'dry_run': True, 'datasets': summaries,
                          'total_selected': sum(r['selected_attachments'] for r in summaries),
                          'missing_sources': missing_sources}), flush=True)
    if missing_sources:
        return 1
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
