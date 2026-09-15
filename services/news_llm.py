"""자동·수동 AI 뉴스 생성에서 공유하는 LLM 호출 정책입니다."""

import os

from google import genai
from google.genai import types
from openai import OpenAI


GEMINI_MODEL = 'gemini-2.5-flash'
OPENAI_MODEL = 'gpt-4o-mini'
AI_REQUEST_TIMEOUT_SECONDS = 20


def call_news_llm(prompt, *, is_json=False, log=None):
    """Gemini를 우선 호출하고 실패하면 OpenAI로 전환합니다."""
    gemini_key = os.getenv('GEMINI_API_KEY')
    openai_key = os.getenv('OPENAI_API_KEY')
    if not gemini_key and not openai_key:
        raise RuntimeError('AI 뉴스 생성 API 키가 설정되어 있지 않습니다.')

    if gemini_key:
        try:
            if log:
                log('   [안내] Gemini API 호출 시도 중...')
            config = None
            if is_json:
                config = types.GenerateContentConfig(
                    response_mime_type='application/json'
                )
            request_options = {
                'model': GEMINI_MODEL,
                'contents': prompt,
            }
            if config is not None:
                request_options['config'] = config
            response = genai.Client(
                api_key=gemini_key,
                http_options={'timeout': AI_REQUEST_TIMEOUT_SECONDS * 1_000},
            ).models.generate_content(**request_options)
            if response.text and response.text.strip():
                return response.text.strip()
            raise RuntimeError('Gemini가 빈 응답을 반환했습니다.')
        except Exception as error:
            if log:
                log(f'   ⚠️ Gemini 호출 실패: {error}')
            if not openai_key:
                raise RuntimeError('AI 뉴스 생성에 실패했습니다.') from error

    if log:
        log(f'   -> OpenAI({OPENAI_MODEL})로 백업 호출 시도 중...')
    try:
        response = OpenAI(
            api_key=openai_key,
            timeout=AI_REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        ).chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{'role': 'user', 'content': prompt}],
            response_format={'type': 'json_object'} if is_json else None,
        )
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise RuntimeError('AI가 기사 응답을 생성하지 않았습니다.')
        return content.strip()
    except Exception as error:
        if log:
            log(f'   ❌ OpenAI 호출 실패: {error}')
        raise RuntimeError('AI 뉴스 생성에 실패했습니다.') from error
