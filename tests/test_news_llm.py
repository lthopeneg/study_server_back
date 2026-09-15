import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services.news_llm import call_news_llm


def gemini_response(text):
    return SimpleNamespace(text=text)


def openai_response(text):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
    )


class NewsLlmTests(unittest.TestCase):
    @patch('services.news_llm.OpenAI')
    @patch('services.news_llm.genai.Client')
    def test_uses_gemini_first(self, gemini_client, openai_client):
        gemini_client.return_value.models.generate_content.return_value = (
            gemini_response(' Gemini 결과 ')
        )

        with patch.dict('os.environ', {'GEMINI_API_KEY': 'gemini'}, clear=True):
            result = call_news_llm('prompt')

        self.assertEqual(result, 'Gemini 결과')
        openai_client.assert_not_called()

    @patch('services.news_llm.OpenAI')
    @patch('services.news_llm.genai.Client')
    def test_falls_back_to_openai_and_preserves_json_mode(
        self, gemini_client, openai_client
    ):
        gemini_client.return_value.models.generate_content.side_effect = RuntimeError(
            'unavailable'
        )
        openai_client.return_value.chat.completions.create.return_value = (
            openai_response('{"selected": true}')
        )

        with patch.dict(
            'os.environ',
            {'GEMINI_API_KEY': 'gemini', 'OPENAI_API_KEY': 'openai'},
            clear=True,
        ):
            result = call_news_llm('prompt', is_json=True)

        self.assertEqual(result, '{"selected": true}')
        gemini_config = (
            gemini_client.return_value.models.generate_content.call_args.kwargs['config']
        )
        self.assertEqual(gemini_config.response_mime_type, 'application/json')
        openai_call = openai_client.return_value.chat.completions.create.call_args
        self.assertEqual(
            openai_call.kwargs['response_format'], {'type': 'json_object'}
        )

    @patch('services.news_llm.OpenAI')
    @patch('services.news_llm.genai.Client')
    def test_uses_openai_when_only_openai_key_exists(
        self, gemini_client, openai_client
    ):
        openai_client.return_value.chat.completions.create.return_value = (
            openai_response('OpenAI 결과')
        )

        with patch.dict('os.environ', {'OPENAI_API_KEY': 'openai'}, clear=True):
            result = call_news_llm('prompt')

        self.assertEqual(result, 'OpenAI 결과')
        gemini_client.assert_not_called()

    def test_rejects_missing_api_keys(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'API 키'):
                call_news_llm('prompt')

    @patch('services.news_llm.OpenAI')
    @patch('services.news_llm.genai.Client')
    def test_reports_failure_when_both_providers_fail(
        self, gemini_client, openai_client
    ):
        gemini_client.return_value.models.generate_content.side_effect = RuntimeError(
            'gemini failure'
        )
        openai_client.return_value.chat.completions.create.side_effect = RuntimeError(
            'openai failure'
        )

        with patch.dict(
            'os.environ',
            {'GEMINI_API_KEY': 'gemini', 'OPENAI_API_KEY': 'openai'},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, 'AI 뉴스 생성에 실패'):
                call_news_llm('prompt')

