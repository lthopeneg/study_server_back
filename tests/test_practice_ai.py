import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services.practice_ai import (
    generate_problem_draft,
    generate_scenario_draft,
    repair_problem_draft,
    review_problem_draft,
)


class PracticeAiTests(unittest.TestCase):
    @patch('services.practice_ai.collect_research_context', return_value='연구노트 내용')
    @patch('services.practice_ai.OpenAI')
    def test_expands_short_scenario_and_generates_extra_request(self, openai_class, _collect_context):
        client = MagicMock()
        client.responses.create.return_value = SimpleNamespace(output_text=json.dumps({
            'scenario': '쇼핑몰 상품 이미지 처리 API가 외부 판매자의 데이터를 수신합니다.',
            'extra_request': '두 유형의 파일 구조와 데이터 흐름을 일관되게 구성합니다.',
        }))
        openai_class.return_value = client

        with patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'}):
            result = generate_scenario_draft(
                language='C#', major_topic='입력데이터 검증 및 표현',
                minor_topic='메모리 버퍼 오버플로우', difficulty='beginner',
                minimum_files=3, target_blank_count=3, scenario_seed='쇼핑몰 API',
                extra_request_seed='', reference_scope='latest', model='gpt-5.6-luna',
            )

        self.assertIn('쇼핑몰', result['scenario'])
        self.assertTrue(result['extra_request'])
        request = client.responses.create.call_args.kwargs
        self.assertEqual(request['model'], 'gpt-5.6-luna')
        self.assertIn('쇼핑몰 API', request['input'])
        self.assertEqual(request['max_output_tokens'], 2_500)

    @patch('services.practice_ai.collect_research_context', return_value='연구노트 내용')
    @patch('services.practice_ai.OpenAI')
    def test_replaces_exact_blank_count_with_server_target_policy(self, openai_class, _collect_context):
        client = MagicMock()
        client.responses.create.return_value = SimpleNamespace(output_text=json.dumps({
            'scenario': '배송 조회 API가 외부 운송장 식별자를 처리합니다.',
            'extra_request': (
                '두 유형의 흐름을 일관되게 구성합니다. '
                '2유형은 보안상 의미 있는 빈칸을 정확히 3개 설정합니다. '
                '한글 주석으로 업무 흐름을 설명합니다.'
            ),
        }))
        openai_class.return_value = client

        with patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'}):
            result = generate_scenario_draft(
                language='Python', major_topic='입력데이터 검증 및 표현',
                minor_topic='메모리 버퍼 오버플로우', difficulty='beginner',
                minimum_files=3, target_blank_count=3, scenario_seed='',
                extra_request_seed='', reference_scope='latest', model='gpt-5.6-luna',
            )

        self.assertNotIn('정확히 3개', result['extra_request'])
        self.assertIn('빈칸 3개 이상을 목표', result['extra_request'])
        self.assertIn('자연스럽게 만들 수 없다면 더 적게', result['extra_request'])
        self.assertIn('한글 주석', result['extra_request'])

    @patch('services.practice_ai.collect_research_context', return_value='연구노트 내용')
    @patch('services.practice_ai.secrets.choice', return_value='보안 관제 센터의 이벤트 수집 및 분석 업무')
    @patch('services.practice_ai.OpenAI')
    def test_assigns_non_commerce_domain_when_scenario_is_empty(
        self, openai_class, choice, _collect_context,
    ):
        client = MagicMock()
        client.responses.create.return_value = SimpleNamespace(output_text=json.dumps({
            'scenario': '보안 관제 센터가 외부 장비의 이벤트 데이터를 수신합니다.',
            'extra_request': '두 유형의 데이터 흐름을 일관되게 구성합니다.',
        }))
        openai_class.return_value = client

        with patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'}):
            result = generate_scenario_draft(
                language='Python', major_topic='입력데이터 검증 및 표현',
                minor_topic='메모리 버퍼 오버플로우', difficulty='beginner',
                minimum_files=3, target_blank_count=3, scenario_seed='',
                extra_request_seed='', reference_scope='latest', model='gpt-5.6-luna',
            )

        self.assertIn('보안 관제', result['scenario'])
        choice.assert_called_once()
        request = client.responses.create.call_args.kwargs['input']
        self.assertIn('서버가 지정한 업무 분야: 보안 관제 센터', request)
        self.assertIn('온라인 쇼핑몰, 상품 등록, 상품 이미지, 주문 또는 배송 업무로 바꾸지 않습니다.', request)

    @patch('services.practice_ai.OpenAI')
    def test_reviews_generated_problem_quality(self, openai_class):
        client = MagicMock()
        client.responses.create.return_value = SimpleNamespace(output_text=json.dumps({
            'score': 88,
            'blocking_issues': [],
            'warnings': ['빈칸 하나가 다소 쉽습니다.'],
            'summary': '보안 흐름은 올바릅니다.',
        }))
        openai_class.return_value = client

        with patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'}):
            result = review_problem_draft(
                {'variants': []},
                language='Python', major_topic='입력데이터 검증 및 표현',
                minor_topic='메모리 버퍼 오버플로우', difficulty='beginner',
                model='gpt-5.6-luna',
            )

        self.assertEqual(result['score'], 88)
        self.assertEqual(result['blocking_issues'], [])
        self.assertIn('빈칸', result['warnings'][0])
        prompt = client.responses.create.call_args.kwargs['input']
        self.assertIn('Sink에 도달하지 않으면 관련 없는 정답', prompt)
        self.assertIn('가장 핵심적인 함수·메서드·조건 요소', prompt)
        self.assertIn('정답이 주변에 보인다는 이유만으로 blocking_issues', prompt)

    @patch('services.practice_ai.OpenAI')
    def test_repairs_invalid_generated_problem_with_validation_error(self, openai_class):
        client = MagicMock()
        client.responses.create.return_value = SimpleNamespace(output_text=json.dumps({
            'variants': [{'problem_type': 'secure_blank'}],
        }))
        openai_class.return_value = client

        with patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'}):
            result = repair_problem_draft(
                {'variants': []},
                '원본 데이터 길이 검사가 누락되었습니다.',
                language='Python',
                runtime_platform=None,
                project_type=None,
                major_topic='입력데이터 검증 및 표현',
                minor_topic='메모리 버퍼 오버플로우',
                difficulty='beginner',
                minimum_files=3,
                target_blank_count=3,
                scenario='',
                extra_request='',
                reference_scope='latest',
                model='gpt-5.6-luna',
            )

        self.assertEqual(result['variants'][0]['problem_type'], 'secure_blank')
        prompt = client.responses.create.call_args.kwargs['input']
        self.assertIn('원본 데이터 길이 검사가 누락', prompt)
        self.assertIn('기존 JSON', prompt)

    @patch('services.practice_ai.collect_research_context', return_value='연구노트 내용')
    @patch('services.practice_ai.OpenAI')
    def test_uses_selected_model_with_responses_api(self, openai_class, _collect_context):
        client = MagicMock()
        client.responses.create.return_value = SimpleNamespace(
            output_text=json.dumps({'variants': []}),
        )
        openai_class.return_value = client

        with patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'}):
            result = generate_problem_draft(
                language='Python',
                runtime_platform=None,
                project_type=None,
                major_topic='입력데이터 검증 및 표현',
                minor_topic='SQL 삽입',
                difficulty='beginner',
                minimum_files=3,
                target_blank_count=3,
                scenario='',
                extra_request='',
                reference_scope='latest',
                model='gpt-5.6-luna',
            )

        self.assertEqual(result, {'variants': []})
        request = client.responses.create.call_args.kwargs
        self.assertEqual(request['model'], 'gpt-5.6-luna')
        self.assertEqual(request['text'], {'format': {'type': 'json_object'}})
        self.assertIn('Sink까지 전달되는 값의 유입 라인만', request['input'])
        self.assertIn('answers 배열의 첫 번째 빈칸', request['input'])
        self.assertIn('가장 핵심적인 함수·메서드·조건 요소', request['input'])

    @patch('services.practice_ai.collect_research_context', return_value='연구노트 내용')
    @patch('services.practice_ai.OpenAI')
    def test_adds_csharp_runtime_conditions_to_prompt(self, openai_class, _collect_context):
        client = MagicMock()
        client.responses.create.return_value = SimpleNamespace(output_text=json.dumps({'variants': []}))
        openai_class.return_value = client

        with patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'}):
            generate_problem_draft(
                language='C#',
                runtime_platform='dotnet_framework',
                project_type='aspnet_mvc5',
                major_topic='입력데이터 검증 및 표현',
                minor_topic='SQL 삽입',
                difficulty='beginner',
                minimum_files=3,
                target_blank_count=4,
                scenario='',
                extra_request='',
                reference_scope='latest',
                model='gpt-5.6-luna',
            )

        prompt = client.responses.create.call_args.kwargs['input']
        self.assertIn('실행 환경: .NET Framework', prompt)
        self.assertIn('프로젝트 유형: ASP.NET MVC 5', prompt)
        self.assertIn('의미 있는 빈칸을 4개 이상 만드는 것을 목표', prompt)
        self.assertIn('각각 최소 1개', prompt)

    @patch('services.practice_ai.collect_research_context', return_value='연구노트 내용')
    @patch('services.practice_ai.OpenAI')
    def test_requests_transformed_korean_scenario_comments(self, openai_class, _collect_context):
        client = MagicMock()
        client.responses.create.return_value = SimpleNamespace(output_text=json.dumps({'variants': []}))
        openai_class.return_value = client

        with patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'}):
            generate_problem_draft(
                language='Python',
                runtime_platform=None,
                project_type=None,
                major_topic='입력데이터 검증 및 표현',
                minor_topic='SQL 삽입',
                difficulty='beginner',
                minimum_files=3,
                target_blank_count=3,
                scenario='직원 검색 시스템에서 부서명으로 검색한다.',
                extra_request='',
                reference_scope='latest',
                model='gpt-5.6-luna',
            )

        prompt = client.responses.create.call_args.kwargs['input']
        self.assertIn('원문을 그대로 복사하지 말고', prompt)
        self.assertIn('자연스럽게 재구성한 한글 주석', prompt)
        self.assertIn('line_selection과 secure_blank 양쪽 코드', prompt)
        self.assertIn('정답 라인·빈칸 정답을 직접 알려주거나', prompt)

    @patch('services.practice_ai.collect_research_context', return_value='연구노트 내용')
    @patch('services.practice_ai.OpenAI')
    def test_requests_framework_project_type_auto_selection(self, openai_class, _collect_context):
        client = MagicMock()
        client.responses.create.return_value = SimpleNamespace(output_text=json.dumps({
            'project_type': 'aspnet_web_api2',
            'variants': [],
        }))
        openai_class.return_value = client

        with patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'}):
            result = generate_problem_draft(
                language='C#',
                runtime_platform='dotnet_framework',
                project_type='auto',
                major_topic='입력데이터 검증 및 표현',
                minor_topic='메모리 버퍼 오버플로우',
                difficulty='beginner',
                minimum_files=3,
                target_blank_count=3,
                scenario='외부 장비가 JSON으로 패킷을 전송한다.',
                extra_request='',
                reference_scope='latest',
                model='gpt-5.6-luna',
            )

        self.assertEqual(result['project_type'], 'aspnet_web_api2')
        prompt = client.responses.create.call_args.kwargs['input']
        self.assertIn('시나리오 기반 자동 선택', prompt)
        self.assertIn('ASP.NET Web API 2(aspnet_web_api2)', prompt)
        self.assertIn('최상위 project_type', prompt)


if __name__ == '__main__':
    unittest.main()
