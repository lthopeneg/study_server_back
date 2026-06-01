import os
import feedparser
import pymysql
from dotenv import load_dotenv
from datetime import datetime

# 로컬 테스트용 .env 로드 (GitHub Actions 운영 환경에서는 시크릿 변수를 쓰므로 무시됨)
load_dotenv()

# 한국 주요 보안 뉴스 RSS 피드 목록 (보안뉴스, 데일리시큐)
RSS_URLS = [
    ("보안뉴스", "https://www.boannews.com/media/news_rss.xml"),
    ("데일리시큐", "https://www.dailysecu.com/rss/allArticle.xml")
]

def get_db_connection():
    # .env(로컬) 또는 깃허브 시크릿(운영)에서 가져온 오라클 DB 정보로 직접 연결
    return pymysql.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        port=int(os.getenv("DB_PORT", 3306)),
        cursorclass=pymysql.cursors.DictCursor
    )

def crawl_and_save():
    print(f"[{datetime.now()}] 보안뉴스 크롤링 시작...")
    
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            new_count = 0
            for source, url in RSS_URLS:
                print(f"-> {source} 피드 가져오는 중...")
                feed = feedparser.parse(url)
                
                # 최신 기사들을 하나씩 확인
                for entry in feed.entries:
                    title = entry.title
                    link = entry.link
                    # RSS 특성상 pubDate 키값이 다를 수 있으므로 예외처리
                    pub_date = getattr(entry, 'published', getattr(entry, 'pubDate', ''))
                    
                    # 1. 중복 확인 로직 (이미 DB에 저장된 뉴스 링크면 통과)
                    sql_check = "SELECT id FROM security_news WHERE link = %s"
                    cursor.execute(sql_check, (link,))
                    if cursor.fetchone():
                        continue  
                        
                    # 2. 처음 보는 새 기사면 DB에 저장 (Insert)
                    sql_insert = """
                        INSERT INTO security_news (title, link, pub_date, source)
                        VALUES (%s, %s, %s, %s)
                    """
                    cursor.execute(sql_insert, (title, link, pub_date, source))
                    new_count += 1
            
            connection.commit()
            print(f"[{datetime.now()}] 크롤링 완료! 오라클 DB에 새로 추가된 기사: {new_count}건")
    except Exception as e:
        print(f"크롤링 중 에러 발생: {e}")
    finally:
        connection.close()

if __name__ == "__main__":
    crawl_and_save()
