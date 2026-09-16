import ipaddress
import socket
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from services.news_llm import call_news_llm


PROMPT_PATH = Path(__file__).resolve().parent.parent / 'News_prompt' / 'make_news_prompt.txt'
MAX_ARTICLE_BODY_LENGTH = 5_000
MAX_ARTICLE_RESPONSE_BYTES = 2 * 1024 * 1024
ALLOWED_ARTICLE_CONTENT_TYPES = {'text/html', 'application/xhtml+xml'}


def validate_public_news_url(url):
    parsed = urlparse(url)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or
            parsed.username is not None or parsed.password is not None or
            parsed.port not in {None, 80, 443}):
        raise ValueError('기사 원문 주소가 올바르지 않습니다.')
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))
    except socket.gaierror as error:
        raise ValueError('기사 원문 서버 주소를 확인할 수 없습니다.') from error
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('공개 인터넷 주소의 기사만 작성할 수 있습니다.')


def validate_connected_peer(response):
    """Recheck the address actually used after DNS resolution to limit rebinding."""
    connection = getattr(response.raw, '_connection', None)
    peer_socket = getattr(connection, 'sock', None)
    if peer_socket is None:
        raw = getattr(getattr(getattr(response.raw, '_fp', None), 'fp', None), 'raw', None)
        peer_socket = getattr(raw, '_sock', None)
    if peer_socket is None:
        raise ValueError('기사 원문 서버 연결 주소를 확인할 수 없습니다.')
    try:
        peer_address = ipaddress.ip_address(peer_socket.getpeername()[0])
    except (OSError, ValueError, TypeError) as error:
        raise ValueError('기사 원문 서버 연결 주소를 확인할 수 없습니다.') from error
    if not peer_address.is_global:
        response.close()
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
            stream=True,
        )
        validate_connected_peer(response)
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
    content_type = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
    if content_type not in ALLOWED_ARTICLE_CONTENT_TYPES:
        raise ValueError('HTML 형식의 기사 원문만 가져올 수 있습니다.')
    declared_length = response.headers.get('Content-Length')
    if declared_length and int(declared_length) > MAX_ARTICLE_RESPONSE_BYTES:
        raise ValueError('기사 원문 응답 크기가 너무 큽니다.')
    chunks = []
    received = 0
    for chunk in response.iter_content(chunk_size=16 * 1024):
        if not chunk:
            continue
        received += len(chunk)
        if received > MAX_ARTICLE_RESPONSE_BYTES:
            raise ValueError('기사 원문 응답 크기가 너무 큽니다.')
        chunks.append(chunk)
    soup = BeautifulSoup(b''.join(chunks), 'html.parser')
    paragraphs = [
        paragraph.get_text(' ', strip=True)
        for paragraph in soup.find_all('p')
    ]
    body = '\n'.join(text for text in paragraphs if len(text) > 20)
    if len(body.strip()) < 50:
        raise ValueError('기사 본문을 충분히 가져오지 못했습니다.')
    return body[:MAX_ARTICLE_BODY_LENGTH]


def write_news_article(title, url):
    body = fetch_article_body(url)
    prompt_template = PROMPT_PATH.read_text(encoding='utf-8')
    safe_url = url.replace('http://', 'https://', 1)
    prompt = f'{prompt_template}\n\n[원문 제목]: {title}\n[원문 URL]: {safe_url}\n[본문 내용]:\n{body}'
    return call_news_llm(prompt)
