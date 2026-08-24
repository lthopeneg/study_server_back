import unittest
from datetime import date
from unittest.mock import Mock, patch

import crawl_news


def make_list_html(items, total=None):
    rows = []
    for idx, title, published_at in items:
        rows.append(
            f"""
            <li class="altlist-webzine-item">
              <div class="altlist-webzine-content">
                <h2 class="altlist-subject">
                  <a href="https://www.boannews.com/news/articleView.html?idxno={idx}">
                    {title}
                  </a>
                </h2>
                <p class="altlist-summary">저장하면 안 되는 목록 요약문</p>
                <div class="altlist-info">
                  <div class="altlist-info-item">기자</div>
                  <div class="altlist-info-item">{published_at}</div>
                </div>
              </div>
            </li>
            """
        )
    return (
        f'<h1>전체기사 총 <strong>{len(items) if total is None else total}</strong>건</h1>'
        f'<article id="section-list"><ul>{"".join(rows)}</ul></article>'
    ).encode()


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, *_):
        return None

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return FakeCursor(self.rows)


class CrawlNewsTests(unittest.TestCase):
    def test_date_range_backfills_after_latest_stored_date(self):
        connection = FakeConnection([{"pub_date": "2026-08-19T12:00:00+09:00"}])

        start_date, end_date = crawl_news.get_boannews_date_range(
            connection, date(2026, 8, 23)
        )

        self.assertEqual(start_date, date(2026, 8, 20))
        self.assertEqual(end_date, date(2026, 8, 23))

    def test_date_range_limits_backfill_to_seven_days(self):
        connection = FakeConnection([{"pub_date": "2026-07-01T12:00:00+09:00"}])

        start_date, _ = crawl_news.get_boannews_date_range(
            connection, date(2026, 8, 23)
        )

        self.assertEqual(start_date, date(2026, 8, 17))

    @patch("crawl_news.requests.get")
    def test_boannews_list_collects_only_target_dates(self, mock_get):
        first_response = Mock(content=make_list_html([(1, "누락 보충 기사", "08-22 10:00")]))
        first_response.raise_for_status.return_value = None
        second_response = Mock(content=make_list_html([(2, "전날 기사", "08-23 18:00")]))
        second_response.raise_for_status.return_value = None
        mock_get.side_effect = [first_response, second_response]

        entries = crawl_news.fetch_boannews_entries(
            date(2026, 8, 22), date(2026, 8, 23)
        )

        self.assertEqual([entry[0] for entry in entries], ["누락 보충 기사", "전날 기사"])
        self.assertNotIn("저장하면 안 되는 목록 요약문", repr(entries))
        self.assertEqual(mock_get.call_count, 2)

    def test_list_date_handles_year_boundary(self):
        parsed = crawl_news.parse_boannews_list_date("12-31 23:50", date(2027, 1, 1))

        self.assertEqual(parsed, date(2026, 12, 31))

    def test_paging_json_keeps_only_metadata(self):
        normalized = crawl_news.normalize_boannews_json_item(
            {
                "idxno": "145000",
                "title": "%ED%8E%98%EC%9D%B4%EC%A7%95+%EA%B8%B0%EC%82%AC",
                "viewDate": "08-23",
                "viewTime": "09:30",
                "body": "저장하면 안 되는 기사 본문",
            },
            date(2026, 8, 24),
        )

        self.assertEqual(
            normalized[:3],
            (
                "페이징 기사",
                "https://www.boannews.com/news/articleView.html?idxno=145000",
                "2026-08-23T09:30:00+09:00",
            ),
        )
        self.assertNotIn("기사 본문", repr(normalized))


if __name__ == "__main__":
    unittest.main()
