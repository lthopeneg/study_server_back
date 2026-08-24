import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

import routes.practice as practice_route
from routes.practice import serialize_problem_summary, validate_variant


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


if __name__ == '__main__':
    unittest.main()
