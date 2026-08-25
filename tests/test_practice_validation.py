import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

import routes.practice as practice_route
from routes.practice import (
    grade_problem_submission,
    normalize_generated_blank_answers,
    serialize_admin_problem_detail,
    serialize_public_problem_detail,
    serialize_problem_summary,
    validate_generated_variants,
    validate_variant,
)


class PracticeValidationTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def test_accepts_line_selection_files_and_answers(self):
        variant, error = validate_variant(
            {
                'problem_type': 'line_selection',
                'files': [
                    {'filename': 'a.py', 'content': 'first\nsecond', 'hint': '힌트'},
                    {'filename': 'b.py', 'content': 'value = input()', 'hint': ''},
                ],
                'answers': [
                    {'filename': 'a.py', 'line': 2},
                    {'filename': 'b.py', 'line': 1},
                ],
            },
            'line_selection',
        )

        self.assertIsNone(error)
        self.assertEqual(len(variant['files']), 2)
        self.assertEqual(variant['answers'][0], {'filename': 'a.py', 'line': 2})

    def test_rejects_blank_answer_without_four_underscores(self):
        variant, error = validate_variant(
            {
                'problem_type': 'secure_blank',
                'files': [{'filename': 'a.py', 'content': 'safe_call()', 'hint': ''}],
                'answers': [{'filename': 'a.py', 'line': 1, 'answer': 'validate()'}],
            },
            'secure_blank',
        )

        self.assertIsNone(variant)
        self.assertIn('언더바 4개', error)

    def test_applies_one_hint_to_all_files_in_variant(self):
        variant, error = validate_variant(
            {
                'problem_type': 'line_selection',
                'hint': '이 유형이 공유하는 힌트',
                'files': [
                    {'filename': 'app.py', 'content': 'first'},
                    {'filename': 'service.py', 'content': 'second'},
                ],
                'answers': [{'filename': 'app.py', 'line': 1}],
            },
            'line_selection',
        )

        self.assertIsNone(error)
        self.assertTrue(all(file['hint'] == '이 유형이 공유하는 힌트' for file in variant['files']))

    def test_rejects_path_like_filename(self):
        variant, error = validate_variant(
            {
                'problem_type': 'line_selection',
                'files': [{'filename': '../a.py', 'content': 'value = 1', 'hint': ''}],
                'answers': [{'filename': '../a.py', 'line': 1}],
            },
            'line_selection',
        )

        self.assertIsNone(variant)
        self.assertIn('파일명', error)

    def test_rejects_korean_filename(self):
        variant, error = validate_variant(
            {
                'problem_type': 'line_selection',
                'files': [{'filename': '무제.py', 'content': 'value = 1', 'hint': ''}],
                'answers': [{'filename': '무제.py', 'line': 1}],
            },
            'line_selection',
        )

        self.assertIsNone(variant)
        self.assertIn('영문', error)

    def test_serializes_problem_status(self):
        result = serialize_problem_summary(SimpleNamespace(
            id=7,
            title='SQL 삽입 문제',
            language='Python',
            major_topic='입력데이터 검증 및 표현',
            minor_topic='SQL 삽입',
            difficulty='beginner',
            creation_method='manual',
            status='draft',
            created_at=None,
            updated_at=None,
        ))

        self.assertEqual(result['id'], 7)
        self.assertEqual(result['status'], 'draft')

    def test_rejects_unknown_publish_status(self):
        with (
            self.app.test_request_context(json={'status': 'hidden'}),
            patch.object(practice_route, 'get_jwt_identity', return_value='admin'),
            patch.object(practice_route, 'get_admin_user', return_value=SimpleNamespace(id=1)),
        ):
            response, status = practice_route.update_problem_status.__wrapped__(7)

        self.assertEqual(status, 400)
        self.assertEqual(response.get_json()['status'], 'error')

    def test_rejects_unknown_public_problem_language(self):
        with self.app.test_request_context('/?language=Ruby'):
            response, status = practice_route.get_published_problem_sets()

        self.assertEqual(status, 400)
        self.assertEqual(response.get_json()['status'], 'error')

    def test_accepts_generated_pair_with_minimum_files(self):
        generated = {
            'variants': [
                {
                    'problem_type': 'line_selection',
                    'hint': '입력부터 실행 지점까지 살펴보세요.',
                    'files': [
                        {'filename': 'app.py', 'content': 'value = input()'},
                        {'filename': 'db.py', 'content': 'execute(value)'},
                    ],
                    'answers': [{'filename': 'db.py', 'line': 1}],
                },
                {
                    'problem_type': 'secure_blank',
                    'hint': '데이터와 명령을 분리하는 방법을 적용하세요.',
                    'files': [
                        {'filename': 'app.py', 'content': 'value = input()'},
                        {'filename': 'db.py', 'content': 'execute(____)'},
                    ],
                    'answers': [{'filename': 'db.py', 'line': 1, 'answer': 'query, (value,)'}],
                },
            ],
        }

        variants = validate_generated_variants(generated, 2)

        self.assertEqual([item['problem_type'] for item in variants], ['line_selection', 'secure_blank'])
        self.assertEqual(len(variants[0]['files']), 2)

    def test_rejects_generated_variant_below_minimum_files(self):
        generated = {
            'variants': [
                {
                    'problem_type': 'line_selection',
                    'hint': '힌트',
                    'files': [{'filename': 'app.py', 'content': 'value = input()'}],
                    'answers': [{'filename': 'app.py', 'line': 1}],
                },
                {
                    'problem_type': 'secure_blank',
                    'hint': '힌트',
                    'files': [{'filename': 'app.py', 'content': 'value = ____'}],
                    'answers': [{'filename': 'app.py', 'line': 1, 'answer': 'input()'}],
                },
            ],
        }

        with self.assertRaisesRegex(ValueError, '최소 파일 수'):
            validate_generated_variants(generated, 2)

    def test_corrects_generated_blank_answer_line_from_code(self):
        variant = {
            'problem_type': 'secure_blank',
            'hint': '힌트',
            'files': [{'filename': 'buffer.py', 'content': 'first\nif size > ____:\n    raise ValueError()'}],
            'answers': [{'filename': 'buffer.py', 'line': 3, 'answer': 'BUFFER_SIZE'}],
        }

        normalized = normalize_generated_blank_answers(variant)

        self.assertEqual(normalized['answers'][0]['line'], 2)

    def test_normalizes_long_generated_blank_marker(self):
        variant = {
            'problem_type': 'secure_blank',
            'hint': '힌트',
            'files': [{'filename': 'buffer.py', 'content': 'if size > ________:\n    raise ValueError()'}],
            'answers': [{'filename': 'buffer.py', 'line': 1, 'answer': 'BUFFER_SIZE'}],
        }

        normalized = normalize_generated_blank_answers(variant)

        self.assertIn('____', normalized['files'][0]['content'])
        self.assertNotIn('________', normalized['files'][0]['content'])

    def make_published_problem(self):
        return SimpleNamespace(
            id=12,
            title='내부 제목',
            language='Python',
            major_topic='입력데이터 검증 및 표현',
            minor_topic='메모리 버퍼 오버플로우',
            difficulty='intermediate',
            creation_method='ai',
            status='published',
            scenario='네트워크 패킷을 처리합니다.',
            created_at=None,
            updated_at=None,
            variants=[
                SimpleNamespace(
                    problem_type='line_selection',
                    answers_json='[{"filename":"buffer.py","line":2}]',
                    files=[SimpleNamespace(
                        id=1, filename='buffer.py', content='packet = receive()\ncopy(packet)',
                        hint='입력 흐름을 확인하세요.', display_order=0,
                    )],
                ),
                SimpleNamespace(
                    problem_type='secure_blank',
                    answers_json='[{"filename":"buffer.py","line":2,"answer":"BUFFER_SIZE"}]',
                    files=[SimpleNamespace(
                        id=2, filename='buffer.py', content='packet = receive()\nif len(packet) > ____:',
                        hint='버퍼의 크기를 확인하세요.', display_order=0,
                    )],
                ),
            ],
        )

    def test_public_detail_does_not_include_answers(self):
        detail = serialize_public_problem_detail(self.make_published_problem())

        self.assertEqual(detail['id'], 12)
        self.assertEqual(detail['variants'][0]['files'][0]['content'], 'packet = receive()\ncopy(packet)')
        self.assertNotIn('answers', detail['variants'][0])

    def test_admin_detail_includes_saved_answers(self):
        detail = serialize_admin_problem_detail(self.make_published_problem())

        line_variant = next(item for item in detail['variants'] if item['problem_type'] == 'line_selection')
        blank_variant = next(item for item in detail['variants'] if item['problem_type'] == 'secure_blank')
        self.assertEqual(line_variant['answers'], [{'filename': 'buffer.py', 'line': 2}])
        self.assertEqual(blank_variant['answers'][0]['answer'], 'BUFFER_SIZE')

    def test_grades_complete_problem_submission(self):
        result = grade_problem_submission(self.make_published_problem(), [
            {
                'problem_type': 'line_selection',
                'answers': [{'filename': 'buffer.py', 'line': 2}],
            },
            {
                'problem_type': 'secure_blank',
                'answers': [{'filename': 'buffer.py', 'line': 2, 'answer': ' BUFFER_SIZE '}],
            },
        ])

        self.assertTrue(result['correct'])
        self.assertTrue(all(item['correct'] for item in result['variants']))

    def test_marks_wrong_answers_without_revealing_correct_answer(self):
        result = grade_problem_submission(self.make_published_problem(), [
            {'problem_type': 'line_selection', 'answers': [{'filename': 'buffer.py', 'line': 1}]},
            {'problem_type': 'secure_blank', 'answers': [{'filename': 'buffer.py', 'line': 2, 'answer': 'packet'}]},
        ])

        self.assertFalse(result['correct'])
        self.assertFalse(result['variants'][0]['correct'])
        self.assertFalse(result['variants'][1]['answers'][0]['correct'])
        self.assertNotIn('expected_answer', result['variants'][1]['answers'][0])


if __name__ == '__main__':
    unittest.main()
