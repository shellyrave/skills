"""Behavioral checks for lesson handoffs; no external services or lesson generation."""
import copy
import unittest

from validate_lesson import STAGES, validate


def package():
    return {
        'schema_version': '1.0', 'lesson_id': 'example', 'mode': 'autonomous',
        'scope': {'kind': 'full_lesson', 'chapter_ids': []},
        'brief': {'title': 'שיעור לדוגמה', 'audience': 'מאמנים', 'focus': 'בירור',
                  'learning_outcomes': ['להכיר את תפקיד המאמן'], 'include': [], 'exclude': [], 'preserve': []},
        'sources': [{'id': 'source-1', 'location': 'provided-source.txt', 'kind': 'text',
                     'role': 'lesson_content', 'status': 'available', 'read': True, 'version': None, 'notes': ''}],
        'questions': [], 'duration': {'target_minutes': 5, 'estimated': False}, 'explicit_slide_count': 1,
        'chapters': [{'id': 'chapter-a', 'title': 'פרק א', 'outcome': 'הבנה'}],
        'slides': [{'id': 'slide-a', 'chapter_id': 'chapter-a', 'title': 'כותרת מאושרת',
                    'points': ['נקודה מאושרת'], 'questions': ['מה אתם מזהים?'], 'activity': None,
                    'speaker_notes': [], 'visuals': [], 'source_ids': ['source-1'], 'duration_minutes': 5,
                    'approved_fields': ['title', 'points'], 'content_version': 1, 'rendered_content_version': 1}],
        'stages': {name: {'status': 'completed', 'notes': []} for name in STAGES},
        'delivery': {'status': 'ready', 'artifacts': [{'kind': 'pptx', 'location': 'lesson.pptx',
                                                     'slide_ids': ['slide-a']}], 'assumptions': [], 'limitations': []}
    }


class HandoffTests(unittest.TestCase):
    def assertHas(self, fragment, errors):
        self.assertTrue(any(fragment in error for error in errors), errors)

    def test_complete_package(self):
        self.assertEqual(validate(package()), [])

    def test_partial_intake_is_valid_and_not_forced_to_render(self):
        data = package()
        data['slides'] = []
        data['delivery'] = {'status': 'draft', 'artifacts': [], 'assumptions': [], 'limitations': []}
        for name in STAGES[1:]:
            data['stages'][name]['status'] = 'pending'
        self.assertEqual(validate(data), [])

    def test_original_map_cannot_be_replaced_by_question_only(self):
        data = package()
        data['slides'][0]['visuals'] = [{'source_id': None, 'asset_path': None, 'kind': 'map',
                                       'required': True, 'status': 'missing', 'alt': 'מפת מקור'}]
        self.assertHas('required-visual-unavailable', validate(data))
        data['delivery']['status'] = 'needs_input'
        data['stages']['presentation']['status'] = 'needs_input'
        data['stages']['review']['status'] = 'pending'
        self.assertEqual(validate(data), [])

    def test_missing_visual_is_not_available_via_missing_source(self):
        data = package()
        data['sources'][0].update(status='missing', read=False)
        data['slides'][0]['visuals'] = [{'source_id': 'source-1', 'asset_path': None, 'kind': 'map',
                                       'required': True, 'status': 'available', 'alt': None}]
        self.assertHas('visual-source-unavailable', validate(data))

    def test_scope_prevents_unrequested_next_chapter(self):
        data = package()
        data['chapters'].append({'id': 'chapter-b', 'title': 'פרק ב', 'outcome': 'הבנה'})
        data['scope'] = {'kind': 'chapter', 'chapter_ids': ['chapter-b']}
        self.assertHas('outside-chapter-scope', validate(data))

    def test_open_blocker_prevents_ready(self):
        data = package()
        data['questions'] = [{'id': 'q1', 'question': 'איפה המפה?', 'reason': 'נדרש מקור',
                              'blocking_stages': ['presentation'], 'blocking_slide_ids': ['slide-a'],
                              'status': 'open', 'answer': None}]
        errors = validate(data)
        self.assertHas('ready-with-blocking-question', errors)
        self.assertHas('blocked-stage-marked-completed', errors)

    def test_nonblocking_optional_question_does_not_stop_delivery(self):
        data = package()
        data['questions'] = [{'id': 'q1', 'question': 'להוסיף דוגמה בעתיד?', 'reason': 'אופציונלי',
                              'blocking_stages': [], 'blocking_slide_ids': [], 'status': 'open', 'answer': None}]
        self.assertEqual(validate(data), [])

    def test_timings_include_all_allocations(self):
        data = package()
        data['slides'][0]['duration_minutes'] = 4
        self.assertHas('duration-mismatch', validate(data))

    def test_no_invented_sixty_minute_target(self):
        data = package()
        data['duration'] = {'target_minutes': None, 'estimated': True}
        data['slides'][0]['duration_minutes'] = 11
        self.assertEqual(validate(data), [])

    def test_explicit_slide_count_is_enforced(self):
        data = package()
        data['explicit_slide_count'] = 2
        self.assertHas('explicit-slide-count-mismatch', validate(data))

    def test_dangling_sources_are_detected(self):
        data = package()
        data['slides'][0]['source_ids'] = ['unknown']
        self.assertHas('unknown-source', validate(data))

    def test_source_must_have_been_read(self):
        data = package()
        data['sources'][0]['read'] = False
        self.assertHas('intake-source-not-read', validate(data))

    def test_questions_cannot_be_answered_by_elapsed_time(self):
        data = package()
        data['questions'] = [{'id': 'q1', 'question': 'מה המטרה?', 'reason': 'מהותי',
                              'blocking_stages': [], 'blocking_slide_ids': [], 'status': 'answered', 'answer': None}]
        self.assertHas('answer-missing', validate(data))

    def test_approved_text_remains_unchanged(self):
        old = package()
        new = copy.deepcopy(old)
        new['slides'][0].update(title='כותרת חדשה', content_version=2, rendered_content_version=2)
        self.assertHas('approved-field-changed', validate(new, old))
        self.assertEqual(validate(new, old, ['slide-a.title']), [])

    def test_question_change_requires_new_version_and_render(self):
        old = package()
        new = copy.deepcopy(old)
        new['slides'][0]['questions'] = ['מה השתנה?']
        self.assertHas('content-version-not-increased', validate(new, old))
        new['slides'][0]['content_version'] = 2
        self.assertHas('stale-render', validate(new, old))
        new['slides'][0]['rendered_content_version'] = 2
        self.assertEqual(validate(new, old), [])

    def test_approval_cannot_be_silently_discarded(self):
        old = package()
        new = copy.deepcopy(old)
        new['slides'][0]['approved_fields'] = []
        self.assertHas('approval-removed:slide-a.title', validate(new, old))
        self.assertHas('approval-removed:slide-a.points', validate(new, old))

    def test_removed_approved_slide_requires_explicit_override(self):
        old = package()
        new = copy.deepcopy(old)
        new['slides'] = []
        new['delivery']['status'] = 'draft'
        new['delivery']['artifacts'] = []
        self.assertHas('approved-slide-removed', validate(new, old))
        self.assertEqual(validate(new, old, ['slide-a.__delete__']), [])

    def test_image_only_export_does_not_count_as_editable_deck(self):
        data = package()
        data['delivery']['artifacts'][0]['kind'] = 'pdf'
        self.assertHas('editable-deck-coverage', validate(data))

    def test_unknown_contract_version_is_rejected(self):
        data = package()
        data['schema_version'] = '2.0'
        self.assertHas('schema:', validate(data))

    def test_duplicate_slide_ids_are_rejected(self):
        data = package()
        data['slides'].append(copy.deepcopy(data['slides'][0]))
        self.assertHas('duplicate-id:slides', validate(data))


if __name__ == '__main__':
    unittest.main()
