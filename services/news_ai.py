import os
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from google import genai
from openai import OpenAI


PROMPT_PATH = Path(__file__).resolve().parent.parent / 'News_prompt' / 'make_news_prompt.txt'
MAX_ARTICLE_BODY_LENGTH = 5_000
AI_REQUEST_TIMEOUT_SECONDS = 20


def validate_public_news_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('기사 원문 주소가 올바르지 않습니다.')
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))
    except socket.gaierror as error:
        raise ValueError('기사 원문 서버 주소를 확인할 수 없습니다.') from error
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('공개 인터넷 주소의 기사만 작성할 수 있습니다.')


def fetch_article_body(url):
    current_url = url
    response = None
    for _ in range(4):
        validate_public_news_url(current_url)
        response = requests.get(
            current_url,
            headers={'User-Agent': 'Mozilla/5.0 (compatible; SecureCodeSpace/1.0)'},
            timeout=10,
            allow_redirects=False,
        )
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get('Location')
            if not location:
                raise ValueError('기사 원문 이동 주소가 올바르지 않습니다.')
            current_url = urljoin(current_url, location)
            continue
        break
    if response is None or response.is_redirect or response.is_permanent_redirect:
        raise ValueError('기사 원문 이동 횟수가 너무 많습니다.')
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'html.parser')
    paragraphs = [
        paragraph.get_text(' ', strip=True)
        for paragraph in soup.find_all('p')
    ]
    body = '\n'.join(text for text in paragraphs if len(text) > 20)
    if len(body.strip()) < 50:
        raise ValueError('기사 본문을 충분히 가져오지 못했습니다.')
    return body[:MAX_ARTICLE_BODY_LENGTH]


def call_news_writer(prompt):
    gemini_key = os.getenv('GEMINI_API_KEY')
    openai_key = os.getenv('OPENAI_API_KEY')
    if not gemini_key and not openai_key:
        raise RuntimeError('AI 뉴스 생성 API 키가 설정되어 있지 않습니다.')

    if gemini_key:
        try:
            response = genai.Client(
                api_key=gemini_key,
                http_options={'timeout': AI_REQUEST_TIMEOUT_SECONDS * 1_000},
            ).models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
            )
            if response.text and response.text.strip():
                return response.text.strip()
        except Exception:
            if not openai_key:
                raise RuntimeError('AI 뉴스 생성에 실패했습니다.')

    try:
        response = OpenAI(
            api_key=openai_key,
            timeout=AI_REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        ).chat.completions.create(
            model='gpt-4o-mini',
            messages=[{'role': 'user', 'content': prompt}],
        )
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise RuntimeError('AI가 기사 본문을 생성하지 않았습니다.')
        return content.strip()
    except RuntimeError:
        raise
    except Exception as error:
        raise RuntimeError('AI 뉴스 생성에 실패했습니다.') from error


def write_news_article(title, url):
    body = fetch_article_body(url)
    prompt_template = PROMPT_PATH.read_text(encoding='utf-8')
    safe_url = url.replace('http://', 'https://', 1)
    prompt = f'{prompt_template}\n\n[원문 제목]: {title}\n[원문 URL]: {safe_url}\n[본문 내용]:\n{body}'
    return call_news_writer(prompt)
