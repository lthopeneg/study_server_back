import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services.practice_ai import generate_problem_draft


class PracticeAiTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
