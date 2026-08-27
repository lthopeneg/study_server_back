import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

import routes.practice as practice_route
from routes.practice import (
    build_problem_archive,
    build_generation_warnings,
    build_generated_blank_hint,
    grade_problem_submission,
    normalize_generated_blank_answers,
    normalize_generated_line_answers,
    serialize_admin_problem_detail,
    serialize_public_problem_detail,
    serialize_problem_summary,
    validate_csharp_environment,
    validate_generated_variants,
    validate_generated_line_hint,
    validate_variant,
)


def make_structured_hint(source=0, validation_failure=0, sink=0):
    return (
        '- 입력 데이터 유입 지점 (Source)\n'
        '  외부 데이터가 처음 들어오는 흐름을 확인하세요.\n'
        f'  정답 라인 수: {source}개\n\n'
        '- 안전하지 않은 처리 지점 (Validation Failure)\n'
        '  데이터가 충분히 보호되지 않는 과정을 확인하세요.\n'
        f'  정답 라인 수: {validation_failure}개\n\n'
        '- 위험한 최종 사용 지점 (Sink)\n'
        '  데이터가 최종적으로 사용되는 지점을 확인하세요.\n'
        f'  정답 라인 수: {sink}개'
    )


class PracticeValidationTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def test_rejects_framework_request_with_modern_dotnet_selection(self):
        error = validate_csharp_environment(
            'C#', 'dotnet', 'console', 'C# .NET Framework 환경으로 작성합니다.',
        )

        self.assertIn('.NET Framework', error)

    def test_accepts_matching_framework_selection(self):
        error = validate_csharp_environment(
            'C#', 'dotnet_framework', 'console', 'ASP.NET Core 전용 API는 사용하지 않습니다.',
        )

        self.assertIsNone(error)

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
        self.assertEqual(variant['answers'][0], {'filename': 'a.py', 'line': 2, 'code': 'second'})

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
            runtime_platform=None,
            project_type=None,
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
                    'hint': make_structured_hint(source=1, validation_failure=1, sink=1),
                    'files': [
                        {'filename': 'app.py', 'content': 'value = input()\nvalidated = value.strip()'},
                        {'filename': 'db.py', 'content': 'execute(validated)'},
                    ],
                    'answers': [
                        {'filename': 'app.py', 'line': 1, 'code': 'value = input()', 'role': 'source'},
                        {'filename': 'app.py', 'line': 2, 'code': 'validated = value.strip()', 'role': 'validation_failure'},
                        {'filename': 'db.py', 'line': 1, 'code': 'execute(validated)', 'role': 'sink'},
                    ],
                },
                {
                    'problem_type': 'secure_blank',
                    'hint': '데이터와 명령을 분리하는 방법을 적용하세요.',
                    'files': [
                        {'filename': 'app.py', 'content': 'value = input()'},
                        {'filename': 'db.py', 'content': 'execute(query, ____)'},
                    ],
                    'answers': [{
                        'filename': 'db.py', 'line': 1, 'answer': 'value',
                        'hint': '조회문과 별도로 전달해야 하는 입력 데이터 변수를 사용하세요.',
                    }],
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
                    'hint': make_structured_hint(source=1, validation_failure=1, sink=1),
                    'files': [{'filename': 'app.py', 'content': 'value = input()\nquery = "SELECT " + value\nexecute(query)'}],
                    'answers': [
                        {'filename': 'app.py', 'line': 1, 'code': 'value = input()', 'role': 'source'},
                        {'filename': 'app.py', 'line': 2, 'code': 'query = "SELECT " + value', 'role': 'validation_failure'},
                        {'filename': 'app.py', 'line': 3, 'code': 'execute(query)', 'role': 'sink'},
                    ],
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

    def test_rejects_generated_blank_expression_answer(self):
        generated = {
            'variants': [
                {
                    'problem_type': 'line_selection',
                    'hint': make_structured_hint(source=1, validation_failure=1, sink=1),
                    'files': [{'filename': 'buffer.py', 'content': 'data = receive()\nsize = len(data)\ncopy(destination, data)'}],
                    'answers': [
                        {'filename': 'buffer.py', 'line': 1, 'code': 'data = receive()', 'role': 'source'},
                        {'filename': 'buffer.py', 'line': 2, 'code': 'size = len(data)', 'role': 'validation_failure'},
                        {'filename': 'buffer.py', 'line': 3, 'code': 'copy(destination, data)', 'role': 'sink'},
                    ],
                },
                {
                    'problem_type': 'secure_blank',
                    'hint': '힌트',
                    'files': [{'filename': 'buffer.py', 'content': 'size = ____'}],
                    'answers': [{
                        'filename': 'buffer.py', 'line': 1, 'answer': 'min(size, limit)',
                        'hint': '두 크기 중 작은 값을 구하는 표현식이 필요합니다.',
                    }],
                },
            ],
        }

        with self.assertRaisesRegex(ValueError, '단일 식별자'):
            validate_generated_variants(generated, 1)

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

    def test_builds_blank_hint_by_file_and_occurrence(self):
        variant = {
            'problem_type': 'secure_blank',
            'files': [
                {'filename': 'config.py', 'content': 'first = ____\nsecond = ____'},
                {'filename': 'service.py', 'content': 'handler = ____'},
            ],
            'answers': [
                {'filename': 'config.py', 'line': 1, 'answer': 'PrimaryValue', 'hint': '첫 설정값을 나타내는 상수를 입력하세요.'},
                {'filename': 'config.py', 'line': 2, 'answer': 'SecondaryValue', 'hint': '두 번째 설정값을 나타내는 상수를 입력하세요.'},
                {'filename': 'service.py', 'line': 1, 'answer': 'ErrorHandler', 'hint': '오류를 처리하는 클래스 이름을 입력하세요.'},
            ],
        }

        result = build_generated_blank_hint(variant)

        self.assertIn('- config.py (첫 번째 빈칸)', result['hint'])
        self.assertIn('- config.py (두 번째 빈칸)', result['hint'])
        self.assertIn('- service.py (첫 번째 빈칸)', result['hint'])

    def test_rejects_blank_hint_that_reveals_answer(self):
        variant = {
            'problem_type': 'secure_blank',
            'files': [{'filename': 'service.py', 'content': 'handler = ____'}],
            'answers': [{
                'filename': 'service.py', 'line': 1, 'answer': 'ErrorHandler',
                'hint': 'ErrorHandler 클래스 이름을 입력하세요.',
            }],
        }

        with self.assertRaisesRegex(ValueError, '정답을 직접'):
            build_generated_blank_hint(variant)

    def test_corrects_generated_line_answer_using_code_anchor(self):
        variant = {
            'problem_type': 'line_selection',
            'hint': '힌트',
            'files': [{'filename': 'buffer.py', 'content': '# 설명\ndestination = make_buffer()\ncopy(destination, data)'}],
            'answers': [{'filename': 'buffer.py', 'line': 1, 'code': 'copy(destination, data)'}],
        }

        normalized = normalize_generated_line_answers(variant)

        self.assertEqual(normalized['answers'][0]['line'], 3)

    def test_rejects_generated_line_answer_without_code_anchor(self):
        variant = {
            'problem_type': 'line_selection',
            'files': [{'filename': 'buffer.py', 'content': 'copy(destination, data)'}],
            'answers': [{'filename': 'buffer.py', 'line': 1}],
        }

        with self.assertRaisesRegex(ValueError, '실제 코드 한 줄'):
            normalize_generated_line_answers(variant)

    def test_rejects_generated_line_hint_with_wrong_role_count(self):
        variant = {
            'problem_type': 'line_selection',
            'hint': make_structured_hint(source=2, validation_failure=1, sink=1),
            'answers': [
                {'filename': 'app.py', 'line': 1, 'code': 'value = input()', 'role': 'source'},
                {'filename': 'db.py', 'line': 1, 'code': 'query = build(value)', 'role': 'validation_failure'},
                {'filename': 'db.py', 'line': 2, 'code': 'execute(query)', 'role': 'sink'},
            ],
        }

        with self.assertRaisesRegex(ValueError, '실제 정답 수'):
            validate_generated_line_hint(variant)

    def test_rejects_generated_line_hint_when_any_required_role_is_missing(self):
        variant = {
            'problem_type': 'line_selection',
            'hint': make_structured_hint(source=1, sink=1),
            'answers': [
                {'filename': 'app.py', 'line': 1, 'code': 'value = input()', 'role': 'source'},
                {'filename': 'db.py', 'line': 1, 'code': 'execute(value)', 'role': 'sink'},
            ],
        }

        with self.assertRaisesRegex(ValueError, '각각 최소 1개'):
            validate_generated_line_hint(variant)

    def test_warns_when_generated_blank_count_is_below_target(self):
        variants = [
            {'problem_type': 'line_selection', 'answers': [{}, {}, {}]},
            {'problem_type': 'secure_blank', 'answers': [{}, {}]},
        ]

        warnings = build_generation_warnings(variants, 3)

        self.assertEqual(len(warnings), 1)
        self.assertRegex(warnings[0], r'3.*2')

    def test_does_not_warn_when_generated_blank_count_meets_target(self):
        variants = [
            {'problem_type': 'secure_blank', 'answers': [{}, {}, {}]},
        ]

        self.assertEqual(build_generation_warnings(variants, 3), [])

    def test_rejects_comment_as_line_selection_answer(self):
        variant, error = validate_variant(
            {
                'problem_type': 'line_selection',
                'hint': '힌트',
                'files': [{'filename': 'buffer.py', 'content': '# 메모리 복사 설명\ncopy(destination, data)'}],
                'answers': [{'filename': 'buffer.py', 'line': 1}],
            },
            'line_selection',
        )

        self.assertIsNone(variant)
        self.assertIn('주석', error)

    def test_builds_completed_blank_line_and_answer_kind(self):
        variant, error = validate_variant(
            {
                'problem_type': 'secure_blank',
                'hint': '힌트',
                'files': [{'filename': 'buffer.py', 'content': 'copy_size = min(packet_size, ____)'}],
                'answers': [{'filename': 'buffer.py', 'line': 1, 'answer': 'BUFFER_SIZE'}],
            },
            'secure_blank',
        )

        self.assertIsNone(error)
        self.assertEqual(variant['answers'][0]['answer_kind'], 'identifier')
        self.assertEqual(variant['answers'][0]['completed_line'], 'copy_size = min(packet_size, BUFFER_SIZE)')

    def make_published_problem(self):
        return SimpleNamespace(
            id=12,
            title='내부 제목',
            language='Python',
            runtime_platform=None,
            project_type=None,
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

    def test_problem_archive_contains_both_types_and_answer_files(self):
        archive = build_problem_archive(self.make_published_problem())

        with zipfile.ZipFile(archive) as zip_file:
            names = set(zip_file.namelist())
            self.assertIn('type1_line_selection/buffer.py', names)
            self.assertIn('problem_info.txt', names)
            self.assertIn('type1_line_selection/hint.txt', names)
            self.assertIn('type1_line_selection/answers.txt', names)
            self.assertIn('type2_secure_blank/buffer.py', names)
            self.assertIn('type2_secure_blank/hint.txt', names)
            self.assertIn('type2_secure_blank/answers.txt', names)
            answers = zip_file.read('type2_secure_blank/answers.txt').decode('utf-8-sig')
            self.assertIn('BUFFER_SIZE', answers)

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
