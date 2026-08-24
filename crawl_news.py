import os
import sys
from datetime import datetime

import feedparser
import pymysql
import requests
from dotenv import load_dotenv

load_dotenv()

RSS_URLS = [
    ("보안뉴스", "https://www.boannews.com/media/news_rss.xml"),
    ("데일리시큐", "https://www.dailysecu.com/rss/allArticle.xml"),
]

REQUEST_TIMEOUT_SECONDS = 20
REQUIRED_DB_ENV = ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")


def validate_environment():
    missing = [name for name in REQUIRED_DB_ENV if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"필수 DB 환경 변수가 없습니다: {', '.join(missing)}")


def get_db_connection():
    validate_environment()
    return pymysql.connect(
        host=os.environ["DB_HOST"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        port=int(os.getenv("DB_PORT") or 3306),
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def fetch_feed(url):
    response = requests.get(
        url,
        headers={"User-Agent": "SECURECODE-SPACE-NewsCrawler/1.0"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    if getattr(feed, "bozo", False) and not feed.entries:
        raise RuntimeError(f"RSS 파싱 실패: {feed.bozo_exception}")
    if not feed.entries:
        raise RuntimeError("RSS에 기사 항목이 없습니다.")
    return feed


def normalize_entry(entry):
    title = str(getattr(entry, "title", "")).strip()
    link = str(getattr(entry, "link", "")).strip()
    pub_date = str(
        getattr(entry, "published", getattr(entry, "pubDate", getattr(entry, "updated", "")))
    ).strip()

    if not title or not link.startswith(("http://", "https://")):
        return None
    if len(link) > 500:
        return None
    return title[:500], link, pub_date[:100]


def save_feed(connection, source, feed):
    inserted_count = 0
    skipped_count = 0
    sql_insert = """
        INSERT INTO security_news (title, link, pub_date, source)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE link = VALUES(link)
    """

    with connection.cursor() as cursor:
        for entry in feed.entries:
            normalized = normalize_entry(entry)
            if not normalized:
                skipped_count += 1
                continue
            title, link, pub_date = normalized
            cursor.execute(sql_insert, (title, link, pub_date, source[:100]))
            inserted_count += cursor.rowcount

    connection.commit()
    return inserted_count, skipped_count


def crawl_and_save():
    print(f"[{datetime.now()}] 보안뉴스 크롤링 시작...")
    connection = get_db_connection()
    total_inserted = 0
    successful_sources = 0
    failed_sources = []

    try:
        for source, url in RSS_URLS:
            print(f"-> {source} 피드 가져오는 중...")
            try:
                feed = fetch_feed(url)
                inserted, skipped = save_feed(connection, source, feed)
                total_inserted += inserted
                successful_sources += 1
                print(f"   {source}: 신규 {inserted}건, 유효하지 않은 항목 {skipped}건")
            except Exception as error:
                connection.rollback()
                failed_sources.append(source)
                print(f"::warning title={source} RSS 수집 실패::{type(error).__name__}: {error}")
    finally:
        connection.close()

    if successful_sources == 0:
        raise RuntimeError("모든 RSS 피드 수집에 실패했습니다.")

    if failed_sources:
        print(f"일부 매체 수집 실패: {', '.join(failed_sources)}")

    print(f"[{datetime.now()}] 크롤링 완료! 새로 추가된 기사: {total_inserted}건")
    return total_inserted


if __name__ == "__main__":
    try:
        crawl_and_save()
    except Exception as error:
        print(f"::error title=보안뉴스 크롤링 실패::{type(error).__name__}: {error}")
        sys.exit(1)
