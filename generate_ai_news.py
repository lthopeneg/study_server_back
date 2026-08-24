import os
import json
import sys
from datetime import datetime, date
from pathlib import Path
import requests
from bs4 import BeautifulSoup
# 🚨 최신 라이브러리로 변경 (pip install google-genai 필요)
from google import genai
from google.genai import types
from openai import OpenAI
import time
from app import app
from extensions import db
from models import SecurityNews, DailyMainNews

# ==========================================
# 1. API 키 및 클라이언트 설정
# ==========================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PROMPT_DIR = Path(__file__).resolve().parent / "News_prompt"

if not GEMINI_API_KEY:
    raise ValueError("환경 변수에 GEMINI_API_KEY가 없습니다!")

# 최신 Gemini 클라이언트 초기화 방식
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

openai_client = None
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ==========================================
# 2. 공통 AI 호출 래퍼 (Fallback 로직)
# ==========================================
def call_llm_with_fallback(prompt, is_json=False):
    """Gemini API를 먼저 시도하고, 에러 발생 시 OpenAI API로 자동 전환합니다."""
    # [1차 시도] Gemini (최신 SDK v1 기준 코드)
    try:
        print("   [안내] Gemini API 호출 시도 중...")
        if is_json:
            response = gemini_client.models.generate_content(
                model='gemini-2.5-flash', # 2026년 기준 안정적인 최신 플래시 모델 권장 (혹은 gemini-1.5-flash)
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
        else:
            response = gemini_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt
            )
        return response.text
    except Exception as gemini_err:
        print(f"   ⚠️ Gemini 호출 실패: {gemini_err}")
        print("   -> OpenAI(gpt-4o-mini)로 백업 호출 시도 중...")
        
        # [2차 시도] OpenAI Fallback
        if not openai_client:
            raise RuntimeError("Gemini가 실패했지만 OPENAI_API_KEY가 설정되어 있지 않아 복구할 수 없습니다.")
            
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"} if is_json else None
            )
            return response.choices[0].message.content
        except Exception as openai_err:
            print(f"   ❌ OpenAI 호출 마저 실패: {openai_err}")
            raise RuntimeError("모든 AI API 호출에 실패했습니다.")

def fetch_pending_news():
    """마지막 AI 기사 생성 이후 수집되어 아직 처리되지 않은 뉴스를 가져옵니다."""
    latest_ai_news = DailyMainNews.query.order_by(DailyMainNews.created_at.desc()).first()
    query = SecurityNews.query
    if latest_ai_news and latest_ai_news.created_at:
        query = query.filter(SecurityNews.created_at > latest_ai_news.created_at)
    news_list = query.order_by(SecurityNews.created_at.asc(), SecurityNews.id.asc()).all()
    return [{"id": n.id, "title": n.title, "link": n.link} for n in news_list]

def scrape_article_body(url):
    """선택된 뉴스 링크로 들어가서 기사 '본문'만 긁어옵니다."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        paragraphs = soup.find_all('p')
        body_text = "\n".join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20])
        return body_text[:5000] 
    except Exception as e:
        print(f"스크래핑 에러 ({url}): {e}")
        return ""

def generate_ai_news():
    with app.app_context():
        today = date.today()
        existing = DailyMainNews.query.filter(db.func.date(DailyMainNews.created_at) == today).first()
        if existing:
            print(f"[{datetime.now()}] 오늘의 AI 메인 뉴스가 이미 작성되어 있습니다.")
            return

        print(f"[{datetime.now()}] 🤖 AI 자동 메인 뉴스 생성 파이프라인 시작 (2단계 압축 선발 + 이중화)...")
        news_list = fetch_pending_news()
        
        if not news_list:
            print("오늘 수집된 뉴스가 없습니다.")
            return

        # ==========================================================
        # Step 1: [예선전] 제목만 보고 TOP 3 선정
        # ==========================================================
        print(f"-> 1단계(예선): 오늘 수집된 {len(news_list)}개의 기사 중 TOP 3 후보 선정 중...")
        
        with open(PROMPT_DIR / 'check_prompt.txt', 'r', encoding='utf-8') as f:
            check_prompt_prelim = f.read()
            
        candidates_text_prelim = "오늘의 기사 전체 목록 (제목만 제공):\n"
        for i, news in enumerate(news_list):
            candidates_text_prelim += f"[{i+1}] 제목: {news['title']}\n    URL: {news['link']}\n"
            
        final_prompt_prelim = f"{check_prompt_prelim}\n\n{candidates_text_prelim}"
        
        response_prelim_text = call_llm_with_fallback(final_prompt_prelim, is_json=True)
        
        try:
            parsed_data = json.loads(response_prelim_text)
            
            # [방어 로직] 구글이 배열 [ ] 로 줬을 때와 OpenAI가 오브젝트 { } 로 줬을 때 모두 호환
            if isinstance(parsed_data, list):
                top3_list = parsed_data
            else:
                # "top3_urls" 키를 찾고, 혹시 다른 이름으로 줬다면 첫 번째 리스트 값을 찾음
                top3_list = parsed_data.get("top3_urls", [])
                if not top3_list:
                    for k, v in parsed_data.items():
                        if isinstance(v, list):
                            top3_list = v
                            break
                            
            print("   [예선 통과] 데이터 확보 완료. URL 추출 중...")
        except Exception as e:
            print("   JSON 파싱 에러 (예선전):", e)
            raise RuntimeError("예선 AI 응답을 JSON으로 해석할 수 없습니다.") from e
            
        print("   (API 호출 속도 조절을 위해 5초 대기합니다...)")
        time.sleep(5)

        # ==========================================================
        # Step 2: [결승전] TOP 3 본문 읽고 최종 1개 선정
        # ==========================================================
        print("-> 2단계(결승): TOP 3 기사 본문 검증 및 최종 우승 기사 선정 중...")
        
        with open(PROMPT_DIR / 'check_prompt_final.txt', 'r', encoding='utf-8') as f:
            check_prompt_final = f.read()
            
        candidates_text_final = "TOP 3 후보 기사 상세 내용:\n"
        
        # [환각 방어 및 재시도 로직] 본문을 가져올 수 있는 정상적인 기사만 결승에 진출시킵니다.
        valid_candidates = []
        for i, item in enumerate(top3_list):
            if isinstance(item, dict):
                url = item.get("selected_url") or item.get("url")
            else:
                url = item  # 문자열인 경우 그대로 url로 인식
                
            if not url:
                continue
            matched_news = next((n for n in news_list if n['link'] == url), None)
            if not matched_news:
                print("   [경고] AI가 뉴스 목록에 없는 URL을 반환하여 제외합니다.")
                continue
            title = matched_news['title']
            
            full_body = scrape_article_body(url)
            
            # 본문이 50자 이하이거나 에러가 났다면 후보에서 즉시 제외 (다음 후보로 넘어감)
            if not full_body or len(full_body.strip()) < 50:
                print(f"   [경고] {title} 스크래핑 실패. 결승 후보에서 제외합니다.")
                continue
                
            valid_candidates.append({
                "title": title,
                "url": url,
                "full_body": full_body
            })
            time.sleep(0.5)
            
        # 살아남은 후보가 하나도 없다면 취소
        if not valid_candidates:
            raise RuntimeError("TOP 3 후보 모두 본문을 가져올 수 없습니다.")
            
        # 살아남은 정상 기사들만 AI에게 제공
        for i, cand in enumerate(valid_candidates):
            short_body = cand['full_body'][:1000] + "..." if len(cand['full_body']) > 1000 else cand['full_body']
            candidates_text_final += f"[{i+1}] 제목: {cand['title']}\n    URL: {cand['url']}\n    본문 내용: {short_body}\n\n"
            
        final_prompt_final = f"{check_prompt_final}\n\n{candidates_text_final}"
        response_final_text = call_llm_with_fallback(final_prompt_final, is_json=True)
        
        try:
            selected_info = json.loads(response_final_text)
            selected_title = selected_info.get("selected_title")
            selected_url = selected_info.get("selected_url")
            selection_reason = selected_info.get("selection_reason")
            if not selected_title or not selected_url:
                raise ValueError("필수 선정 결과가 없습니다.")
            if not any(candidate['url'] == selected_url for candidate in valid_candidates):
                raise ValueError("최종 선정 URL이 검증된 후보 목록에 없습니다.")
            print(f"   [최종 선정 완료] {selected_title}")
        except Exception as e:
            print("   JSON 파싱 에러 (결승전):", e)
            raise RuntimeError("결승 AI 응답이 올바르지 않습니다.") from e
        print("   (API 호출 속도 조절을 위해 5초 대기합니다...)")
        time.sleep(5)
        # ==========================================================
        # Step 3: 최종 기사 요약 및 마크다운 작성
        # ==========================================================
        print("-> 3단계: AI 심층 요약 및 마크다운 기사 작성 중...")
        
        with open(PROMPT_DIR / 'make_news_prompt.txt', 'r', encoding='utf-8') as f:
            make_prompt = f.read()
            
        # 이미 2단계에서 검증해둔 본문(full_body)을 찾아서 재활용합니다 (스크래핑 2번 할 필요 방지)
        article_body = ""
        for cand in valid_candidates:
            if cand['url'] == selected_url:
                article_body = cand['full_body']
                break
                
        if not article_body or len(article_body.strip()) < 50:
            raise RuntimeError("최종 기사 본문을 확보하지 못했습니다.")
            
        safe_url = selected_url.replace('http://', 'https://')
        
        final_prompt_3 = f"{make_prompt}\n\n[원문 제목]: {selected_title}\n[원문 URL]: {safe_url}\n[본문 내용]:\n{article_body}"
        
        final_markdown = call_llm_with_fallback(final_prompt_3, is_json=False)
        if not final_markdown or not final_markdown.strip():
            raise RuntimeError("AI가 기사 본문을 생성하지 않았습니다.")
        
        # ==========================================================
        # Step 4: DB 저장
        # ==========================================================
        print("-> 4단계: DB에 완성된 기사 저장 중...")
        new_article = DailyMainNews(
            title=selected_title,
            content_md=final_markdown,
            original_url=selected_url,
            selection_reason=selection_reason
        )
        try:
            db.session.add(new_article)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        
        print("✅ 2단계 압축 선발 + AI 폴백 이중화 파이프라인이 성공적으로 완료되었습니다!")

if __name__ == "__main__":
    try:
        generate_ai_news()
    except Exception as error:
        print(f"::error title=AI 뉴스 생성 실패::{type(error).__name__}: {error}")
        sys.exit(1)
