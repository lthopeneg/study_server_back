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
                major_topic='입력데이터 검증 및 표현',
                minor_topic='SQL 삽입',
                difficulty='beginner',
                minimum_files=3,
                scenario='',
                extra_request='',
                reference_scope='latest',
                model='gpt-5.6-luna',
            )

        self.assertEqual(result, {'variants': []})
        request = client.responses.create.call_args.kwargs
        self.assertEqual(request['model'], 'gpt-5.6-luna')
        self.assertEqual(request['text'], {'format': {'type': 'json_object'}})


if __name__ == '__main__':
    unittest.main()
