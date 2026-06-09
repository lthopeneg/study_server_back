import os
import json
from datetime import datetime, date
import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
import time
# 기존 app.py에서 필요한 모듈 가져오기
from app import app, db, SecurityNews, DailyMainNews

# ==========================================
# 1. 제미나이(Gemini) API 기본 설정
# ==========================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("환경 변수에 GEMINI_API_KEY가 없습니다!")

genai.configure(api_key=GEMINI_API_KEY)
# 비용 대비 성능이 훌륭한 최신 gemini-1.5-flash 모델을 선택합니다.
model = genai.GenerativeModel('gemini-2.5-flash') # 최신 2.5 flash 모델로 변경!

def fetch_today_news():
    """오늘 DB에 수집된 뉴스 목록의 '제목'과 '링크'만 가져옵니다."""
    today = date.today()
    news_list = SecurityNews.query.filter(db.func.date(SecurityNews.created_at) == today).all()
    return [{"id": n.id, "title": n.title, "link": n.link} for n in news_list]

def scrape_article_body(url):
    """선택된 1개의 뉴스 링크로 들어가서 기사 '본문'만 긁어옵니다."""
    try:
        # 뉴스 사이트들의 봇 차단을 우회하기 위한 기본적인 브라우저 헤더
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # 기사 본문으로 추정되는 <p> 태그 안의 텍스트만 싹싹 긁어 모음
        paragraphs = soup.find_all('p')
        body_text = "\n".join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20])
        
        # 토큰(비용) 낭비를 막기 위해 최대 5000자로 컷!
        return body_text[:5000] 
    except Exception as e:
        print(f"스크래핑 에러 ({url}): {e}")
        return ""

def generate_ai_news():
    # Flask 앱 컨텍스트 안에서 실행 (DB 접근 권한을 얻기 위함)
    with app.app_context():
        # [방어 로직] 오늘 이미 AI 메인 뉴스가 만들어져 있다면 스킵
        today = date.today()
        existing = DailyMainNews.query.filter(db.func.date(DailyMainNews.created_at) == today).first()
        if existing:
            print(f"[{datetime.now()}] 오늘의 AI 메인 뉴스가 이미 작성되어 있습니다.")
            return

        print(f"[{datetime.now()}] 🤖 AI 자동 메인 뉴스 생성 파이프라인 시작...")
        
        news_list = fetch_today_news()
        if not news_list:
            print("오늘 수집된 뉴스가 없어 파이프라인을 종료합니다.")
            return

        # ==========================================================
        # Step 1: 기사 선정 (check_prompt.txt 활용)
        # ==========================================================
        print(f"-> 1단계: 오늘 수집된 {len(news_list)}개의 기사 중 메인 기사 선정 중...")
        
        # 밖(부모 폴더)에 있는 프롬프트 파일 읽기
        with open('News_prompt/check_prompt.txt', 'r', encoding='utf-8') as f:
            check_prompt = f.read()
            
        # 후보 리스트 텍스트화 (모든 기사 본문을 긁어와서 AI에게 판단 근거로 제공!)
        print("   [안내] AI가 낚시성 기사를 거르기 위해 모든 후보 기사의 본문을 읽어오고 있습니다...")
        candidates_text = "오늘의 기사 후보 목록:\n"
        
        for i, news in enumerate(news_list):
            # 1. 기사 링크로 들어가서 본문 스크래핑
            full_body = scrape_article_body(news['link'])
            
            # 2. 너무 길면 토큰 비용과 속도에 부담이 되므로, 핵심이 담긴 앞부분 1000자만 잘라서 제공
            short_body = full_body[:1000] + "..." if len(full_body) > 1000 else full_body
            
            # 3. 프롬프트에 추가
            candidates_text += f"[{i+1}] 제목: {news['title']}\n     URL: {news['link']}\n     본문 내용: {short_body}\n\n"
            
            # 4. 언론사 서버에 무리를 주거나 봇으로 차단당하지 않도록 0.5초씩 쉬어줍니다
            time.sleep(0.5)

        final_prompt_1 = f"{check_prompt}\n\n{candidates_text}"
        
        # 💥 [핵심 기술] Gemini가 딴소리 못하고 무조건 JSON 포맷으로만 대답하게 강제하는 설정
        response_1 = model.generate_content(
            final_prompt_1,
            generation_config=genai.GenerationConfig(response_mime_type="application/json")
        )
        
        try:
            selected_info = json.loads(response_1.text)
            selected_title = selected_info.get("selected_title")
            selected_url = selected_info.get("selected_url")
            selection_reason = selected_info.get("selection_reason") # 👈 [추가] 선정 이유 받기
            print(f"   [선정 완료] {selected_title}")
        except Exception as e:
            print("   JSON 파싱 에러 (AI가 엉뚱한 대답을 함):", e)
            print("   AI 대답:", response_1.text)
            return

        # ==========================================================
        # Step 2: 본문 스크래핑 및 기사 작성 (make_news_prompt.txt 활용)
        # ==========================================================
        print("-> 2단계: 선정된 기사 본문 스크래핑 중...")
        article_body = scrape_article_body(selected_url)
        if not article_body:
            print("본문을 긁어오는 데 실패하여 파이프라인을 종료합니다.")
            return
            
        print("-> 3단계: AI 심층 요약 및 마크다운 기사 작성 중...")
        with open('News_prompt/make_news_prompt.txt', 'r', encoding='utf-8') as f:
            make_prompt = f.read()
        safe_url = selected_url.replace('http://', 'https://')    
        final_prompt_2 = f"{make_prompt}\n\n[원문 제목]: {selected_title}\n[원문 URL]: {selected_url}\n[본문 내용]:\n{article_body}"

        # 작성할 때는 마크다운이 필요하므로 일반 텍스트 모드로 생성
        response_2 = model.generate_content(final_prompt_2)
        final_markdown = response_2.text
        
        # ==========================================================
        # Step 3: DB 저장
        # ==========================================================
        print("-> 4단계: DB에 완성된 기사 저장 중...")
        new_article = DailyMainNews(
            title=selected_title, # AI가 새로 지어준 매력적인 제목
            content_md=final_markdown, # 완성된 예쁜 마크다운 본문
            original_url=selected_url,
            selection_reason=selection_reason
        )
        db.session.add(new_article)
        db.session.commit()
        
        print("✅ 모든 AI 기사 작성 파이프라인이 성공적으로 완료되었습니다!")

if __name__ == "__main__":
    generate_ai_news()
