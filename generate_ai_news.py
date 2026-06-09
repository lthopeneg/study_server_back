import os
import json
from datetime import datetime, date
import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
import time
# 기존 app.py에서 필요한 모듈 가져오기
from app import app, db, SecurityNews, DailyMainNews

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError("환경 변수에 GEMINI_API_KEY가 없습니다!")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

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

        print(f"[{datetime.now()}] 🤖 AI 자동 메인 뉴스 생성 파이프라인 시작 (2단계 압축 선발)...")
        news_list = fetch_today_news()
        
        if not news_list:
            print("오늘 수집된 뉴스가 없습니다.")
            return

        # ==========================================================
        # Step 1: [예선전] 제목만 보고 TOP 3 선정 (스크래핑 X, API 1회 소모)
        # ==========================================================
        print(f"-> 1단계(예선): 오늘 수집된 {len(news_list)}개의 기사 중 TOP 3 후보 선정 중...")
        
        with open('News_prompt/check_prompt.txt', 'r', encoding='utf-8') as f:
            check_prompt_prelim = f.read()
            
        candidates_text_prelim = "오늘의 기사 전체 목록 (제목만 제공):\n"
        for i, news in enumerate(news_list):
            candidates_text_prelim += f"[{i+1}] 제목: {news['title']}\n     URL: {news['link']}\n"
            
        final_prompt_prelim = f"{check_prompt_prelim}\n\n{candidates_text_prelim}"
        
        response_prelim = model.generate_content(
            final_prompt_prelim,
            generation_config=genai.GenerationConfig(response_mime_type="application/json")
        )
        
        try:
            top3_urls = json.loads(response_prelim.text)
            print("   [예선 통과] 3개의 후보 URL 확보 완료")
        except Exception as e:
            print("   JSON 파싱 에러 (예선전):", e)
            print("   응답 내용:", response_prelim.text)
            return
            
        # 구글 API 분당 요청 제한 방어 (5초 휴식)
        print("   (API 호출 속도 조절을 위해 5초 대기합니다...)")
        time.sleep(5)

        # ==========================================================
        # Step 2: [결승전] TOP 3 본문 읽고 최종 1개 선정 (스크래핑 3번, API 1회 소모)
        # ==========================================================
        print("-> 2단계(결승): TOP 3 기사 본문 검증 및 최종 우승 기사 선정 중...")
        
        with open('News_prompt/check_prompt_final.txt', 'r', encoding='utf-8') as f:
            check_prompt_final = f.read()
            
        candidates_text_final = "TOP 3 후보 기사 상세 내용:\n"
        
        for i, item in enumerate(top3_urls):
            url = item.get("selected_url")
            # 전체 뉴스 리스트에서 일치하는 URL의 제목 찾아오기
            title = next((n['title'] for n in news_list if n['link'] == url), "제목 없음")
            
            # 본문 가져와서 1000자로 요약 제공
            full_body = scrape_article_body(url)
            short_body = full_body[:1000] + "..." if len(full_body) > 1000 else full_body
            
            candidates_text_final += f"[{i+1}] 제목: {title}\n     URL: {url}\n     본문 내용: {short_body}\n\n"
            time.sleep(0.5)
            
        final_prompt_final = f"{check_prompt_final}\n\n{candidates_text_final}"
        
        response_final = model.generate_content(
            final_prompt_final,
            generation_config=genai.GenerationConfig(response_mime_type="application/json")
        )
        
        try:
            selected_info = json.loads(response_final.text)
            selected_title = selected_info.get("selected_title")
            selected_url = selected_info.get("selected_url")
            selection_reason = selected_info.get("selection_reason")
            print(f"   [최종 선정 완료] {selected_title}")
        except Exception as e:
            print("   JSON 파싱 에러 (결승전):", e)
            return

        # 구글 API 분당 요청 제한 방어 (5초 휴식)
        print("   (API 호출 속도 조절을 위해 5초 대기합니다...)")
        time.sleep(5)

        # ==========================================================
        # Step 3: 최종 기사 요약 및 마크다운 작성 (API 1회 소모)
        # ==========================================================
        print("-> 3단계: AI 심층 요약 및 마크다운 기사 작성 중...")
        
        with open('News_prompt/make_news_prompt.txt', 'r', encoding='utf-8') as f:
            make_prompt = f.read()
            
        # 본문을 최대 길이(5000자)로 꽉 채워서 요약용으로 다시 가져옴
        article_body = scrape_article_body(selected_url)
        safe_url = selected_url.replace('http://', 'https://')
        
        final_prompt_3 = f"{make_prompt}\n\n[원문 제목]: {selected_title}\n[원문 URL]: {safe_url}\n[본문 내용]:\n{article_body}"
        
        response_3 = model.generate_content(final_prompt_3)
        final_markdown = response_3.text
        
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
        
        print("✅ 2단계 압축 선발 AI 기사 작성 파이프라인이 성공적으로 완료되었습니다!")

if __name__ == "__main__":
    generate_ai_news()
