#!/usr/bin/env python3
"""Validate lesson handoffs. Reads files only; never installs or publishes anything."""

import argparse
import json
from pathlib import Path
import sys

STAGES = ('intake', 'structure', 'content', 'facilitation', 'presentation', 'review')
CONTENT_FIELDS = ('title', 'points', 'questions', 'activity', 'speaker_notes', 'visuals', 'source_ids')
EDITABLE_KINDS = {'live_presentation', 'pptx', 'google_slides'}
SCHEMA_PATH = Path(__file__).resolve().parents[1] / 'references/lesson-package.schema.json'


def validate(data, baseline=None, allow_approved_changes=()):
    """Return errors; callers may supply an earlier package to protect approvals."""
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        raise RuntimeError('Validation requires the jsonschema Python package; nothing was installed.') from exc
    schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
    validator = Draft202012Validator(schema)
    errors = [f"schema:{'/'.join(map(str, e.absolute_path))}: {e.message}"
              for e in sorted(validator.iter_errors(data), key=lambda e: str(list(e.absolute_path)))]
    if errors:
        return errors
    if baseline is not None:
        old_errors = list(validator.iter_errors(baseline))
        if old_errors:
            return ['baseline: the previous package does not satisfy the schema']

    for collection in ('sources', 'chapters', 'slides', 'questions'):
        ids = [item['id'] for item in data[collection]]
        if len(ids) != len(set(ids)):
            errors.append(f'duplicate-id:{collection}')
    source_ids = {s['id'] for s in data['sources']}
    chapter_ids = {c['id'] for c in data['chapters']}
    slides = {s['id']: s for s in data['slides']}
    for source in data['sources']:
        if source['read'] and source['status'] != 'available':
            errors.append(f"unreadable-source-marked-read:{source['id']}")
        if (data['stages']['intake']['status'] == 'completed' and source['status'] == 'available'
                and source['role'] in {'lesson_content', 'method'} and not source['read']):
            errors.append(f"intake-source-not-read:{source['id']}")
    if data['scope']['kind'] == 'chapter':
        selected = set(data['scope']['chapter_ids'])
        if not selected or not selected <= chapter_ids:
            errors.append('scope: chapter selection must name existing chapters')
        for slide in data['slides']:
            if slide['chapter_id'] not in selected:
                errors.append(f"outside-chapter-scope:{slide['id']}")
    for slide in data['slides']:
        sid = slide['id']
        if slide['chapter_id'] not in chapter_ids:
            errors.append(f'unknown-chapter:{sid}')
        for source_id in slide['source_ids']:
            if source_id not in source_ids:
                errors.append(f'unknown-source:{sid}:{source_id}')
        for visual in slide['visuals']:
            source_id = visual['source_id']
            if source_id is not None and source_id not in source_ids:
                errors.append(f'unknown-visual-source:{sid}:{source_id}')
            if visual['required'] and visual['status'] != 'available':
                if data['stages']['presentation']['status'] == 'completed' or data['delivery']['status'] == 'ready':
                    errors.append(f'required-visual-unavailable:{sid}')
            if visual['status'] == 'available' and not visual['asset_path'] and source_id is None:
                errors.append(f'visual-has-no-location:{sid}')
            if visual['status'] == 'available' and source_id is not None:
                source = next(x for x in data['sources'] if x['id'] == source_id) if source_id in source_ids else None
                if source and source['status'] != 'available':
                    errors.append(f'visual-source-unavailable:{sid}')
        if data['stages']['presentation']['status'] == 'completed':
            if slide['rendered_content_version'] != slide['content_version']:
                errors.append(f'stale-render:{sid}')
    for question in data['questions']:
        for sid in question['blocking_slide_ids']:
            if sid not in slides:
                errors.append(f"unknown-question-slide:{question['id']}:{sid}")
        if question['status'] == 'answered' and not question['answer']:
            errors.append(f"answer-missing:{question['id']}")
        if question['status'] == 'open':
            for name in question['blocking_stages']:
                if data['stages'][name]['status'] == 'completed':
                    errors.append(f"blocked-stage-marked-completed:{question['id']}:{name}")

    for artifact in data['delivery']['artifacts']:
        for sid in artifact['slide_ids']:
            if sid not in slides:
                errors.append(f'unknown-artifact-slide:{sid}')
    ready = data['delivery']['status'] == 'ready'
    if ready:
        if not slides:
            errors.append('ready-without-slides')
        if any(data['stages'][name]['status'] != 'completed' for name in STAGES):
            errors.append('ready-with-incomplete-stages')
        if any(q['status'] == 'open' and (q['blocking_stages'] or q['blocking_slide_ids']) for q in data['questions']):
            errors.append('ready-with-blocking-question')
        if any(not s['title'].strip() for s in data['slides']):
            errors.append('ready-with-empty-title')
        if any(s['duration_minutes'] is None for s in data['slides']):
            errors.append('ready-without-slide-timing')
        expected = data['explicit_slide_count']
        if expected is not None and expected != len(slides):
            errors.append('explicit-slide-count-mismatch')
        total = sum(s['duration_minutes'] or 0 for s in data['slides'])
        target = data['duration']['target_minutes']
        if target is not None and abs(total - target) > 0.01:
            errors.append(f'duration-mismatch: allocated={total}, target={target}')
        covered = set()
        for artifact in data['delivery']['artifacts']:
            if artifact['kind'] in EDITABLE_KINDS:
                covered.update(artifact['slide_ids'])
        if not set(slides) <= covered:
            errors.append('ready-without-editable-deck-coverage')

    if baseline is not None:
        allowed = set(allow_approved_changes)
        old_slides = {s['id']: s for s in baseline['slides']}
        for sid, old in old_slides.items():
            new = slides.get(sid)
            if new is None:
                if old['approved_fields'] and f'{sid}.__delete__' not in allowed:
                    errors.append(f'approved-slide-removed:{sid}')
                continue
            for field in old['approved_fields']:
                if old[field] != new[field] and f'{sid}.{field}' not in allowed:
                    errors.append(f'approved-field-changed:{sid}.{field}')
                if field not in new['approved_fields'] and f'{sid}.{field}' not in allowed:
                    errors.append(f'approval-removed:{sid}.{field}')
            changed = any(old[k] != new[k] for k in CONTENT_FIELDS)
            if changed and new['content_version'] <= old['content_version']:
                errors.append(f'content-version-not-increased:{sid}')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--allow-approved-change', action='append', default=[], metavar='SLIDE_ID.FIELD',
                        help='Only for an explicitly authorized change; use ID.__delete__ for an authorized deletion.')
    args = parser.parse_args()
    try:
        data = json.loads(args.package.read_text(encoding='utf-8'))
        baseline = json.loads(args.baseline.read_text(encoding='utf-8')) if args.baseline else None
        errors = validate(data, baseline, args.allow_approved_change)
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if errors:
        print('\n'.join(errors))
        return 1
    print('Lesson package structure and consistency checks passed. Content and visual review are still required.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
