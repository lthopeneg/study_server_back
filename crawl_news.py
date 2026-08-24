import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import unquote_plus, urlparse

import feedparser
import pymysql
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

RSS_URLS = [
    ("데일리시큐", "https://www.dailysecu.com/rss/allArticle.xml"),
]

BOANNEWS_SOURCE = "보안뉴스"
BOANNEWS_LIST_URL = "https://www.boannews.com/news/articleList.html"
BOANNEWS_PAGING_URL = "https://www.boannews.com/news/ajaxArticlePaging.php"
BOANNEWS_MAX_BACKFILL_DAYS = 7
BOANNEWS_MAX_PAGES = 10
KST = timezone(timedelta(hours=9))
REQUEST_TIMEOUT_SECONDS = 20
REQUIRED_DB_ENV = ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")
REQUEST_HEADERS = {"User-Agent": "SECURECODE-SPACE-NewsCrawler/1.0"}
BOANNEWS_REQUEST_HEADERS = {**REQUEST_HEADERS, "Cache-Control": "no-cache"}


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
        headers=REQUEST_HEADERS,
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


def save_entries(connection, source, entries):
    inserted_count = 0
    skipped_count = 0
    sql_insert = """
        INSERT INTO security_news (title, link, pub_date, source)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE link = VALUES(link)
    """

    with connection.cursor() as cursor:
        for entry in entries:
            normalized = entry if isinstance(entry, tuple) else normalize_entry(entry)
            if not normalized:
                skipped_count += 1
                continue
            title, link, pub_date = normalized
            cursor.execute(sql_insert, (title, link, pub_date, source[:100]))
            inserted_count += cursor.rowcount

    connection.commit()
    return inserted_count, skipped_count


def parse_stored_pub_date(value, reference_date):
    """기존 RSS 날짜와 보안뉴스 목록 날짜를 날짜 객체로 정규화합니다."""
    if not value:
        return None

    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass

    try:
        return parsedate_to_datetime(text).date()
    except (TypeError, ValueError, OverflowError):
        pass

    match = re.fullmatch(r"(\d{2})-(\d{2})(?:\s+\d{2}:\d{2})?", text)
    if not match:
        return None

    month, day = map(int, match.groups())
    try:
        parsed = date(reference_date.year, month, day)
    except ValueError:
        return None
    if parsed > reference_date:
        parsed = date(reference_date.year - 1, month, day)
    return parsed


def get_boannews_date_range(connection, target_date):
    """전날을 기본으로 하되 최근 수집 누락은 최대 7일 범위에서 보충합니다."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT pub_date
            FROM security_news
            WHERE source = %s AND pub_date IS NOT NULL AND pub_date <> ''
            ORDER BY created_at DESC
            LIMIT 500
            """,
            (BOANNEWS_SOURCE,),
        )
        stored_dates = [
            parsed
            for row in cursor.fetchall()
            if (parsed := parse_stored_pub_date(row.get("pub_date"), target_date))
        ]

    earliest_allowed = target_date - timedelta(days=BOANNEWS_MAX_BACKFILL_DAYS - 1)
    if not stored_dates:
        return target_date, target_date

    latest_date = max(stored_dates)
    if latest_date >= target_date:
        return target_date, target_date
    return max(latest_date + timedelta(days=1), earliest_allowed), target_date


def parse_boannews_list_date(value, reference_date):
    parsed = parse_stored_pub_date(value, reference_date)
    if not parsed:
        raise ValueError(f"보안뉴스 게시일 형식을 해석할 수 없습니다: {value!r}")
    return parsed


def normalize_boannews_item(item, reference_date):
    link_element = item.select_one("h2.altlist-subject a")
    info_elements = item.select(".altlist-info-item")
    if not link_element or not info_elements:
        return None

    title = link_element.get_text(" ", strip=True)
    link = str(link_element.get("href") or "").strip()
    parsed_url = urlparse(link)
    if (
        not title
        or parsed_url.scheme != "https"
        or parsed_url.netloc != "www.boannews.com"
        or parsed_url.path != "/news/articleView.html"
        or len(link) > 500
    ):
        return None

    raw_date = info_elements[-1].get_text(" ", strip=True)
    published_date = parse_boannews_list_date(raw_date, reference_date)
    published_at = datetime.strptime(
        f"{published_date.year}-{raw_date}", "%Y-%m-%d %H:%M"
    ).replace(tzinfo=KST)
    return title[:500], link, published_at.isoformat(), published_date


def normalize_boannews_json_item(item, reference_date):
    article_id = str(item.get("idxno") or "").strip()
    title = unquote_plus(str(item.get("title") or "")).strip()
    raw_date = f'{item.get("viewDate") or ""} {item.get("viewTime") or ""}'.strip()
    if not article_id.isdigit() or not title or not raw_date:
        return None

    published_date = parse_boannews_list_date(raw_date, reference_date)
    published_at = datetime.strptime(
        f"{published_date.year}-{raw_date}", "%Y-%m-%d %H:%M"
    ).replace(tzinfo=KST)
    link = f"https://www.boannews.com/news/articleView.html?idxno={article_id}"
    return title[:500], link, published_at.isoformat(), published_date


def fetch_boannews_entries(start_date, end_date):
    entries = []
    reference_date = end_date + timedelta(days=1)
    target_date = start_date

    while target_date <= end_date:
        date_text = target_date.isoformat()
        response = requests.get(
            BOANNEWS_LIST_URL,
            params={
                "view_type": "sm",
                "sc_sdate": date_text,
                "sc_edate": date_text,
                "_": int(datetime.now().timestamp()),
            },
            headers=BOANNEWS_REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        items = soup.select("#section-list li.altlist-webzine-item")
        total_element = soup.select_one("h1 strong")
        if not total_element:
            raise RuntimeError(
                f"보안뉴스 목록 구조를 확인할 수 없습니다. (date={date_text})"
            )

        total_text = re.sub(r"\D", "", total_element.get_text())
        total_articles = int(total_text or 0)
        if total_articles > 0 and not items:
            raise RuntimeError(
                f"보안뉴스 기사 항목을 확인할 수 없습니다. (date={date_text})"
            )

        normalized_items = [
            normalize_boannews_item(item, reference_date) for item in items
        ]
        page_count = (total_articles + 19) // 20
        if page_count > BOANNEWS_MAX_PAGES:
            raise RuntimeError(
                f"보안뉴스 {date_text} 기사 수가 안전한 조회 한도를 초과했습니다."
            )

        for page in range(2, page_count + 1):
            response = requests.get(
                BOANNEWS_PAGING_URL,
                params={
                    "total": total_articles,
                    "list_per_page": 20,
                    "page_per_page": 10,
                    "page": page,
                    "view_type": "sm",
                    "sc_sdate": date_text,
                    "sc_edate": date_text,
                },
                headers=BOANNEWS_REQUEST_HEADERS,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
            items = payload.get("data") if payload.get("result") == "success" else None
            if not isinstance(items, list) or not items:
                raise RuntimeError(
                    f"보안뉴스 기사 목록을 확인할 수 없습니다. "
                    f"(date={date_text}, page={page})"
                )
            normalized_items.extend(
                normalize_boannews_json_item(item, reference_date) for item in items
            )

        for normalized in normalized_items:
            if not normalized:
                continue
            title, link, pub_date, published_date = normalized
            if published_date == target_date:
                entries.append((title, link, pub_date))

        target_date += timedelta(days=1)
    return entries


def crawl_and_save():
    print(f"[{datetime.now()}] 보안뉴스 크롤링 시작...")
    connection = get_db_connection()
    total_inserted = 0
    successful_sources = 0
    failed_sources = []

    try:
        target_date = datetime.now(KST).date() - timedelta(days=1)
        print("-> 보안뉴스 목록 가져오는 중...")
        try:
            start_date, end_date = get_boannews_date_range(connection, target_date)
            print(f"   수집 대상: {start_date.isoformat()} ~ {end_date.isoformat()} (KST)")
            entries = fetch_boannews_entries(start_date, end_date)
            inserted, skipped = save_entries(connection, BOANNEWS_SOURCE, entries)
            total_inserted += inserted
            successful_sources += 1
            print(
                f"   보안뉴스: 대상 {len(entries)}건, 신규 {inserted}건, "
                f"유효하지 않은 항목 {skipped}건"
            )
        except Exception as error:
            connection.rollback()
            failed_sources.append(BOANNEWS_SOURCE)
            print(
                f"::warning title=보안뉴스 목록 수집 실패::"
                f"{type(error).__name__}: {error}"
            )

        for source, url in RSS_URLS:
            print(f"-> {source} 피드 가져오는 중...")
            try:
                feed = fetch_feed(url)
                inserted, skipped = save_entries(connection, source, feed.entries)
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
        raise RuntimeError("모든 뉴스 매체 수집에 실패했습니다.")

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
