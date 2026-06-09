import os
import json
from datetime import datetime, date
import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
from openai import OpenAI
import time
# 기존 app.py에서 필요한 모듈 가져오기
from app import app, db, SecurityNews, DailyMainNews

# ==========================================
# 1. API 키 및 클라이언트 설정
# ==========================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError("환경 변수에 GEMINI_API_KEY가 없습니다!")

# Gemini 설정
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel('gemini-1.5-flash')

# OpenAI 설정
openai_client = None
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ==========================================
# 2. 공통 AI 호출 래퍼 (Fallback 로직)
# ==========================================
def call_llm_with_fallback(prompt, is_json=False):
    """Gemini API를 먼저 시도하고, 에러(429 등) 발생 시 OpenAI API로 자동 전환합니다."""
    # [1차 시도] Gemini
    try:
        print("   [안내] Gemini API 호출 시도 중...")
        if is_json:
            response = gemini_model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(response_mime_type="application/json")
            )
        else:
            response = gemini_model.generate_content(prompt)
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

def fetch_today_news():
    """오늘 DB에 수집된 뉴스 목록의 '제목'과 '링크'만 가져옵니다."""
    today = date.today()
    news_list = SecurityNews.query.filter(db.func.date(SecurityNews.created_at) == today).all()
    return [{"id": n.id, "title": n.title, "link": n.link} for n in news_list]

def scrape_article_body(url):
    """선택된 뉴스 링크로 들어가서 기사 '본문'만 긁어옵니다."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        paragraphs = soup.find_all('p')
        body_text = "\n".join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20])
        return body_text[:5000] 
    except Exception as e:
        print(f"스크래핑 에러 ({url}): {e}")
        return ""

def generate_ai_news():
    with app.app_context():
        # [방어 로직] 오늘 이미 AI 메인 뉴스가 만들어져 있다면 스킵
        today = date.today()
        existing = DailyMainNews.query.filter(db.func.date(DailyMainNews.created_at) == today).first()
        if existing:
            print(f"[{datetime.now()}] 오늘의 AI 메인 뉴스가 이미 작성되어 있습니다.")
            return

        print(f"[{datetime.now()}] 🤖 AI 자동 메인 뉴스 생성 파이프라인 시작 (2단계 압축 선발 + 이중화)...")
        news_list = fetch_today_news()
        
        if not news_list:
            print("오늘 수집된 뉴스가 없습니다.")
            return

        # ==========================================================
        # Step 1: [예선전] 제목만 보고 TOP 3 선정
        # ==========================================================
        print(f"-> 1단계(예선): 오늘 수집된 {len(news_list)}개의 기사 중 TOP 3 후보 선정 중...")
        
        with open('News_prompt/check_prompt.txt', 'r', encoding='utf-8') as f:
            check_prompt_prelim = f.read()
            
        candidates_text_prelim = "오늘의 기사 전체 목록 (제목만 제공):\n"
        for i, news in enumerate(news_list):
            candidates_text_prelim += f"[{i+1}] 제목: {news['title']}\n     URL: {news['link']}\n"
            
        final_prompt_prelim = f"{check_prompt_prelim}\n\n{candidates_text_prelim}"
        
        # 🌟 래퍼 함수 사용
        response_prelim_text = call_llm_with_fallback(final_prompt_prelim, is_json=True)
        
        try:
            top3_urls = json.loads(response_prelim_text)
            print("   [예선 통과] 3개의 후보 URL 확보 완료")
        except Exception as e:
            print("   JSON 파싱 에러 (예선전):", e)
            print("   응답 내용:", response_prelim_text)
            return
            
        print("   (API 호출 속도 조절을 위해 5초 대기합니다...)")
        time.sleep(5)

        # ==========================================================
        # Step 2: [결승전] TOP 3 본문 읽고 최종 1개 선정
        # ==========================================================
        print("-> 2단계(결승): TOP 3 기사 본문 검증 및 최종 우승 기사 선정 중...")
        
        with open('News_prompt/check_prompt_final.txt', 'r', encoding='utf-8') as f:
            check_prompt_final = f.read()
            
        candidates_text_final = "TOP 3 후보 기사 상세 내용:\n"
        
        for i, item in enumerate(top3_urls):
            url = item.get("selected_url")
            title = next((n['title'] for n in news_list if n['link'] == url), "제목 없음")
            
            full_body = scrape_article_body(url)
            short_body = full_body[:1000] + "..." if len(full_body) > 1000 else full_body
            
            candidates_text_final += f"[{i+1}] 제목: {title}\n     URL: {url}\n     본문 내용: {short_body}\n\n"
            time.sleep(0.5)
            
        final_prompt_final = f"{check_prompt_final}\n\n{candidates_text_final}"
        
        # 🌟 래퍼 함수 사용
        response_final_text = call_llm_with_fallback(final_prompt_final, is_json=True)
        
        try:
            selected_info = json.loads(response_final_text)
            selected_title = selected_info.get("selected_title")
            selected_url = selected_info.get("selected_url")
            selection_reason = selected_info.get("selection_reason")
            print(f"   [최종 선정 완료] {selected_title}")
        except Exception as e:
            print("   JSON 파싱 에러 (결승전):", e)
            return

        print("   (API 호출 속도 조절을 위해 5초 대기합니다...)")
        time.sleep(5)

        # ==========================================================
        # Step 3: 최종 기사 요약 및 마크다운 작성
        # ==========================================================
        print("-> 3단계: AI 심층 요약 및 마크다운 기사 작성 중...")
        
        with open('News_prompt/make_news_prompt.txt', 'r', encoding='utf-8') as f:
            make_prompt = f.read()
            
        article_body = scrape_article_body(selected_url)
        safe_url = selected_url.replace('http://', 'https://')
        
        final_prompt_3 = f"{make_prompt}\n\n[원문 제목]: {selected_title}\n[원문 URL]: {safe_url}\n[본문 내용]:\n{article_body}"
        
        # 🌟 래퍼 함수 사용 (요약본은 일반 텍스트이므로 is_json=False)
        final_markdown = call_llm_with_fallback(final_prompt_3, is_json=False)
        
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
        db.session.add(new_article)
        db.session.commit()
        
        print("✅ 2단계 압축 선발 + AI 폴백 이중화 파이프라인이 성공적으로 완료되었습니다!")

if __name__ == "__main__":
    generate_ai_news()
