import json
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

import routes.practice as practice_route
from routes.practice import (
    apply_csharp_project_templates,
    build_problem_archive,
    build_generation_warnings,
    build_generation_quality_report,
    build_recoverable_generation_draft,
    build_variant_consistency_check,
    build_generated_blank_hint,
    grade_problem_submission,
    normalize_generated_blank_answers,
    normalize_generated_line_answers,
    resolve_generated_project_type,
    serialize_admin_problem_detail,
    serialize_public_problem_detail,
    serialize_problem_summary,
    validate_csharp_environment,
    validate_delete_problem_ids,
    validate_generated_variants,
    validate_generated_line_hint,
    validate_generated_csharp_project_type,
    validate_csharp_project_dependencies,
    validate_memory_buffer_answer_quality,
    validate_memory_buffer_security,
    is_supported_format_string_answer,
    validate_python_generated_syntax,
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
            'C#', 'dotnet_framework', 'aspnet_mvc5', 'ASP.NET Core 전용 API는 사용하지 않습니다.',
        )

        self.assertIsNone(error)

    def test_accepts_auto_project_type_only_for_framework_ai_generation(self):
        self.assertIsNone(validate_csharp_environment(
            'C#', 'dotnet_framework', 'auto', '외부 장비가 JSON 요청을 보냅니다.', allow_auto=True,
        ))
        self.assertIsNotNone(validate_csharp_environment(
            'C#', 'dotnet', 'auto', '', allow_auto=True,
        ))
        self.assertIsNotNone(validate_csharp_environment(
            'C#', 'dotnet_framework', 'auto', '', allow_auto=False,
        ))

    def test_resolves_framework_project_type_selected_by_ai(self):
        self.assertEqual(resolve_generated_project_type(
            {'project_type': 'aspnet_web_api2'}, 'C#', 'dotnet_framework', 'auto',
        ), 'aspnet_web_api2')
        with self.assertRaisesRegex(ValueError, '프로젝트 유형'):
            resolve_generated_project_type(
                {'project_type': 'aspnet_core_web_api'}, 'C#', 'dotnet_framework', 'auto',
            )

    def test_validates_auto_selected_web_api_structure(self):
        variants = [
            {'files': [
                {'filename': 'PacketsController.cs', 'content': 'using System.Web.Http;\nclass PacketsController : ApiController {}'},
            ]},
        ]

        validate_generated_csharp_project_type(variants, 'aspnet_web_api2')
        with self.assertRaisesRegex(ValueError, '실제 코드 구조'):
            validate_generated_csharp_project_type(variants, 'aspnet_mvc5')

    def test_rejects_python_memory_copy_without_source_length_bound(self):
        variants = [{
            'problem_type': 'secure_blank',
            'files': [{'filename': 'packet.py', 'content': (
                'import ctypes\n'
                'BUFFER_CAPACITY = 64\n'
                'copy_size = min(requested_size, BUFFER_CAPACITY)\n'
                'ctypes.memmove(buffer, packet_data, copy_size)'
            )}],
            'answers': [],
        }]

        with self.assertRaisesRegex(ValueError, '실제 원본 길이'):
            validate_memory_buffer_security(variants, 'Python', '메모리 버퍼 오버플로우')

    def test_accepts_python_memory_copy_with_all_three_bounds(self):
        variants = [{
            'problem_type': 'secure_blank',
            'files': [{'filename': 'packet.py', 'content': (
                'import ctypes\n'
                'BUFFER_CAPACITY = 64\n'
                'copy_size = min(requested_size, len(packet_data), BUFFER_CAPACITY)\n'
                'ctypes.memmove(buffer, packet_data, copy_size)'
            )}],
            'answers': [],
        }]

        validate_memory_buffer_security(variants, 'Python', '메모리 버퍼 오버플로우')

    def test_memory_security_uses_completed_blank_answer_code(self):
        variants = [{
            'problem_type': 'secure_blank',
            'files': [{'filename': 'packet.py', 'content': (
                'import ctypes\n'
                'BUFFER_CAPACITY = 64\n'
                'copy_size = min(requested_size, ____(packet_data), BUFFER_CAPACITY)\n'
                'ctypes.memmove(buffer, packet_data, copy_size)'
            )}],
            'answers': [{'filename': 'packet.py', 'line': 3, 'answer': 'len'}],
        }]

        validate_memory_buffer_security(variants, 'Python', '메모리 버퍼 오버플로우')

    def test_rejects_unrelated_python_memory_validation_answer(self):
        variants = [
            {
                'problem_type': 'line_selection',
                'files': [
                    {'filename': 'routes.py', 'content': 'if image.filename.endswith(".png"):\n    pass'},
                    {'filename': 'buffer.py', 'content': 'ctypes.memmove(buffer, data, size)'},
                ],
                'answers': [
                    {'filename': 'routes.py', 'line': 1, 'code': 'if image.filename.endswith(".png"):', 'role': 'validation_failure'},
                    {'filename': 'buffer.py', 'line': 1, 'code': 'ctypes.memmove(buffer, data, size)', 'role': 'sink'},
                ],
            },
            {
                'problem_type': 'secure_blank',
                'files': [{'filename': 'buffer.py', 'content': 'copy_size = ____(size, len(data), BUFFER_CAPACITY)'}],
                'answers': [{'filename': 'buffer.py', 'line': 1, 'answer': 'min'}],
            },
        ]

        with self.assertRaisesRegex(ValueError, '관련 없는 Validation Failure'):
            validate_memory_buffer_answer_quality(variants, 'Python', '메모리 버퍼 오버플로우')

    def test_rejects_non_core_first_python_memory_blank(self):
        variants = [
            {
                'problem_type': 'line_selection',
                'files': [{'filename': 'buffer.py', 'content': 'ctypes.memmove(buffer, data, size)'}],
                'answers': [{'filename': 'buffer.py', 'line': 1, 'code': 'ctypes.memmove(buffer, data, size)', 'role': 'sink'}],
            },
            {
                'problem_type': 'secure_blank',
                'files': [{'filename': 'buffer.py', 'content': (
                    'if ____(data) > BUFFER_CAPACITY:\n    return\n'
                    'copy_size = min(size, len(data), BUFFER_CAPACITY)'
                )}],
                'answers': [{'filename': 'buffer.py', 'line': 1, 'answer': 'len'}],
            },
        ]

        with self.assertRaisesRegex(ValueError, '첫 번째 빈칸'):
            validate_memory_buffer_answer_quality(variants, 'Python', '메모리 버퍼 오버플로우')

    def test_accepts_core_first_python_memory_blank_and_relevant_answers(self):
        variants = [
            {
                'problem_type': 'line_selection',
                'files': [
                    {'filename': 'routes.py', 'content': 'requested_size = request.form["size"]'},
                    {'filename': 'buffer.py', 'content': (
                        'if requested_size < 0:\n    return\n'
                        'ctypes.memmove(buffer, packet_data, requested_size)'
                    )},
                ],
                'answers': [
                    {'filename': 'routes.py', 'line': 1, 'code': 'requested_size = request.form["size"]', 'role': 'source'},
                    {'filename': 'buffer.py', 'line': 1, 'code': 'if requested_size < 0:', 'role': 'validation_failure'},
                    {'filename': 'buffer.py', 'line': 3, 'code': 'ctypes.memmove(buffer, packet_data, requested_size)', 'role': 'sink'},
                ],
            },
            {
                'problem_type': 'secure_blank',
                'files': [{'filename': 'buffer.py', 'content': (
                    'copy_size = ____(requested_size, len(packet_data), BUFFER_CAPACITY)\n'
                    'ctypes.memmove(buffer, packet_data, copy_size)'
                )}],
                'answers': [{'filename': 'buffer.py', 'line': 1, 'answer': 'min'}],
            },
        ]

        validate_memory_buffer_answer_quality(variants, 'Python', '메모리 버퍼 오버플로우')

    def test_requires_framework_package_restore_metadata(self):
        variants = [{
            'problem_type': 'secure_blank',
            'files': [
                {'filename': 'Packet.csproj', 'content': (
                    '<Project><TargetFrameworkVersion>v4.7.2</TargetFrameworkVersion>'
                    '<Reference Include="System.Web.Http" /></Project>'
                )},
                {'filename': 'packages.config', 'content': '<packages></packages>'},
            ],
            'answers': [],
        }]

        with self.assertRaisesRegex(ValueError, '복원 가능한'):
            validate_csharp_project_dependencies(variants, 'aspnet_web_api2')

    def test_server_injects_recoverable_web_api_project_template(self):
        generated = {'variants': [{
            'problem_type': 'line_selection',
            'files': [
                {'filename': 'Controller.cs', 'content': 'using System.Web.Http; class Controller : ApiController {}'},
                {'filename': 'Bad.csproj', 'content': '<Project Sdk="Microsoft.NET.Sdk" />'},
            ],
            'answers': [],
        }]}

        apply_csharp_project_templates(generated, 'aspnet_web_api2')

        files = generated['variants'][0]['files']
        names = {file['filename'] for file in files}
        self.assertIn('PracticeProblem.csproj', names)
        self.assertIn('packages.config', names)
        self.assertNotIn('Bad.csproj', names)
        validate_csharp_project_dependencies(generated['variants'], 'aspnet_web_api2')

    def test_rejects_invalid_generated_python_syntax(self):
        variants = [{
            'problem_type': 'line_selection',
            'files': [{'filename': 'app.py', 'content': 'def broken(:\n    pass'}],
            'answers': [],
        }]

        with self.assertRaisesRegex(ValueError, 'Python 구문'):
            validate_python_generated_syntax(variants, 'Python')

    def test_warns_when_two_variants_have_unrelated_structure(self):
        variants = [
            {
                'problem_type': 'line_selection',
                'files': [{'filename': 'source.py', 'content': 'def receive_packet():\n    return packet_data'}],
                'answers': [],
            },
            {
                'problem_type': 'secure_blank',
                'files': [{'filename': 'unrelated.py', 'content': 'def calculate_invoice():\n    return total_price'}],
                'answers': [],
            },
        ]

        check = build_variant_consistency_check(variants, 'Python')

        self.assertEqual(check['status'], 'warning')

    def test_quality_report_warns_when_blank_answer_is_visible_elsewhere(self):
        variants = [
            {
                'problem_type': 'line_selection',
                'files': [{'filename': 'packet.py', 'content': 'copy_size = packet_size\ncopy(copy_size)'}],
                'answers': [],
            },
            {
                'problem_type': 'secure_blank',
                'files': [{'filename': 'packet.py', 'content': 'copy_size = 1\ncopy(____)'}],
                'answers': [{'filename': 'packet.py', 'line': 2, 'answer': 'copy_size'}],
            },
        ]

        report = build_generation_quality_report(variants, 1, '메모리 버퍼 오버플로우')

        self.assertEqual(report['status'], 'warning')
        exposure = next(check for check in report['checks'] if check['key'] == 'answer_exposure')
        self.assertEqual(exposure['status'], 'warning')

    def test_accepts_delete_problem_ids(self):
        self.assertEqual(validate_delete_problem_ids([3, 7, 9]), [3, 7, 9])

    def test_rejects_duplicate_delete_problem_ids(self):
        with self.assertRaisesRegex(ValueError, '중복'):
            validate_delete_problem_ids([3, 3])

    def test_rejects_invalid_delete_problem_ids(self):
        for problem_ids in ([], [0], [True], ['1']):
            with self.subTest(problem_ids=problem_ids):
                with self.assertRaises(ValueError):
                    validate_delete_problem_ids(problem_ids)

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

    def test_accepts_limited_format_string_answer_elements(self):
        self.assertTrue(is_supported_format_string_answer('SafeFormat'))
        self.assertTrue(is_supported_format_string_answer('CultureInfo.InvariantCulture'))
        self.assertTrue(is_supported_format_string_answer('"User: {0}"'))
        self.assertFalse(is_supported_format_string_answer('string.Format("{0}", value)'))

    def test_accepts_string_literal_blank_for_format_string_topic(self):
        generated = {
            'variants': [
                {
                    'problem_type': 'line_selection',
                    'hint': make_structured_hint(1, 1, 1),
                    'files': [
                        {'filename': 'Logger.cs', 'content': 'var input = request.Name;\nvar format = input;\nlogger.Write(format);'},
                        {'filename': 'PracticeProblem.csproj', 'content': '<Project></Project>'},
                    ],
                    'answers': [
                        {'filename': 'Logger.cs', 'line': 1, 'code': 'var input = request.Name;', 'role': 'source'},
                        {'filename': 'Logger.cs', 'line': 2, 'code': 'var format = input;', 'role': 'validation_failure'},
                        {'filename': 'Logger.cs', 'line': 3, 'code': 'logger.Write(format);', 'role': 'sink'},
                    ],
                },
                {
                    'problem_type': 'secure_blank',
                    'hint': '힌트',
                    'files': [
                        {'filename': 'Logger.cs', 'content': 'const string SafeFormat = ____;'},
                        {'filename': 'PracticeProblem.csproj', 'content': '<Project></Project>'},
                    ],
                    'answers': [{
                        'filename': 'Logger.cs', 'line': 1, 'answer': '"User: {0}"',
                        'hint': '외부 입력과 분리된 고정 출력 형식을 입력하세요.',
                    }],
                },
            ],
        }

        variants = validate_generated_variants(generated, 1, 'C#', '포맷 스트링 삽입')

        self.assertEqual(variants[1]['answers'][0]['answer_kind'], 'expression')

    def test_builds_recoverable_draft_from_invalid_candidate(self):
        generated = {
            'project_type': 'aspnet_mvc5',
            'variants': [{
                'problem_type': 'secure_blank',
                'hint': '',
                'files': [{'filename': 'Logger.cs', 'content': 'var format = ____;'}],
                'answers': [{'filename': 'Logger.cs', 'line': 1, 'answer': 'string.Format("{0}", value)'}],
            }],
        }

        draft = build_recoverable_generation_draft(generated)

        self.assertEqual(draft['project_type'], 'aspnet_mvc5')
        self.assertEqual(draft['variants'][0]['files'][0]['filename'], 'Logger.cs')
        self.assertEqual(draft['variants'][0]['answers'][0]['line'], 1)

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

    def test_replaces_blank_hint_that_reveals_answer(self):
        variant = {
            'problem_type': 'secure_blank',
            'files': [{'filename': 'service.py', 'content': 'handler = ____'}],
            'answers': [{
                'filename': 'service.py', 'line': 1, 'answer': 'ErrorHandler',
                'hint': 'ErrorHandler 클래스 이름을 입력하세요.',
            }],
        }

        result = build_generated_blank_hint(variant)

        self.assertNotIn('ErrorHandler', result['hint'])
        self.assertTrue(result['answers'][0]['hint'])

    def test_fills_missing_blank_hint_from_code_context(self):
        variant = {
            'problem_type': 'secure_blank',
            'files': [{'filename': 'service.py', 'content': 'safe_size = ____(requested, actual, capacity)'}],
            'answers': [{
                'filename': 'service.py', 'line': 1, 'answer': 'min', 'hint': '',
            }],
        }

        result = build_generated_blank_hint(variant)

        self.assertIn('보안 조치를 직접 수행하는 함수 또는 메서드', result['hint'])
        self.assertEqual(result['answers'][0]['hint'], result['hint'].split('\n  ', 1)[1])

    def test_corrects_generated_line_answer_using_code_anchor(self):
        variant = {
            'problem_type': 'line_selection',
            'hint': '힌트',
            'files': [{'filename': 'buffer.py', 'content': '# 설명\ndestination = make_buffer()\ncopy(destination, data)'}],
            'answers': [{'filename': 'buffer.py', 'line': 1, 'code': 'copy(destination, data)'}],
        }

        normalized = normalize_generated_line_answers(variant)

        self.assertEqual(normalized['answers'][0]['line'], 3)

    def test_removes_exact_duplicate_generated_line_answer(self):
        answer = {'filename': 'buffer.py', 'line': 1, 'code': 'data = input()', 'role': 'source'}
        variant = {
            'problem_type': 'line_selection',
            'files': [{'filename': 'buffer.py', 'content': 'data = input()'}],
            'answers': [answer, dict(answer)],
        }

        normalized = normalize_generated_line_answers(variant)

        self.assertEqual(len(normalized['answers']), 1)

    def test_rejects_conflicting_roles_on_same_generated_line(self):
        variant = {
            'problem_type': 'line_selection',
            'files': [{'filename': 'buffer.py', 'content': 'data = input()'}],
            'answers': [
                {'filename': 'buffer.py', 'line': 1, 'code': 'data = input()', 'role': 'source'},
                {'filename': 'buffer.py', 'line': 2, 'code': 'data = input()', 'role': 'sink'},
            ],
        }

        with self.assertRaisesRegex(ValueError, r'buffer\.py 1번 라인'):
            normalize_generated_line_answers(variant)

    def test_removes_exact_duplicate_generated_blank_answer(self):
        answer = {'filename': 'buffer.py', 'line': 1, 'answer': 'limit', 'hint': '허용 범위를 나타내는 값을 입력하세요.'}
        variant = {
            'problem_type': 'secure_blank',
            'files': [{'filename': 'buffer.py', 'content': 'size = min(length, ____)'}],
            'answers': [answer, dict(answer)],
        }

        normalized = normalize_generated_blank_answers(variant)

        self.assertEqual(len(normalized['answers']), 1)

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
            root = 'practice_problems/python/problem_0012'
            self.assertIn(f'{root}/type_1/files/buffer.py', names)
            self.assertIn(f'{root}/problem_info.txt', names)
            self.assertIn(f'{root}/problem.json', names)
            self.assertIn(f'{root}/type_1/hint.txt', names)
            self.assertIn(f'{root}/type_1/answers.txt', names)
            self.assertIn(f'{root}/type_1/answers.json', names)
            self.assertIn(f'{root}/type_2/files/buffer.py', names)
            self.assertIn(f'{root}/type_2/hint.txt', names)
            self.assertIn(f'{root}/type_2/answers.txt', names)
            self.assertIn(f'{root}/type_2/answers.json', names)
            problem_info = zip_file.read(f'{root}/problem_info.txt').decode('utf-8-sig')
            self.assertIn('언어: Python', problem_info)
            self.assertIn('대주제: 입력데이터 검증 및 표현', problem_info)
            self.assertIn('소주제: 메모리 버퍼 오버플로우', problem_info)
            metadata = json.loads(zip_file.read(f'{root}/problem.json'))
            self.assertEqual(metadata['schema_version'], 1)
            self.assertEqual(metadata['problem_id'], 12)
            self.assertEqual(metadata['title'], '내부 제목')
            self.assertEqual(metadata['language'], 'Python')
            self.assertEqual(metadata['variants']['type_1']['problem_type'], 'line_selection')
            self.assertEqual(metadata['variants']['type_1']['files'], ['buffer.py'])
            answers_json = json.loads(zip_file.read(f'{root}/type_2/answers.json'))
            self.assertEqual(answers_json[0]['answer'], 'BUFFER_SIZE')
            answers = zip_file.read(f'{root}/type_2/answers.txt').decode('utf-8-sig')
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
        self.assertEqual(result['variants'][0]['correct_count'], 0)
        self.assertEqual(result['variants'][0]['answers'], [
            {'filename': 'buffer.py', 'line': 1, 'correct': False},
        ])
        self.assertFalse(result['variants'][1]['answers'][0]['correct'])
        self.assertNotIn('expected_answer', result['variants'][1]['answers'][0])

    def test_marks_each_submitted_line_without_revealing_unselected_answers(self):
        result = grade_problem_submission(self.make_published_problem(), [
            {
                'problem_type': 'line_selection',
                'answers': [
                    {'filename': 'buffer.py', 'line': 2},
                    {'filename': 'buffer.py', 'line': 1},
                ],
            },
            {
                'problem_type': 'secure_blank',
                'answers': [{'filename': 'buffer.py', 'line': 2, 'answer': 'BUFFER_SIZE'}],
            },
        ])

        line_result = result['variants'][0]
        self.assertFalse(line_result['correct'])
        self.assertEqual(line_result['correct_count'], 1)
        self.assertEqual(line_result['expected_count'], 1)
        self.assertEqual(line_result['answers'], [
            {'filename': 'buffer.py', 'line': 2, 'correct': True},
            {'filename': 'buffer.py', 'line': 1, 'correct': False},
        ])


if __name__ == '__main__':
    unittest.main()
