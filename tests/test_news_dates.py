import unittest
from datetime import datetime
from types import SimpleNamespace

from routes.news import parse_news_date


class NewsDateTests(unittest.TestCase):
    def test_parses_iso_date_with_timezone(self):
        news = SimpleNamespace(
            pub_date="2026-08-23T17:47:00+09:00",
            created_at=None,
        )

        self.assertEqual(parse_news_date(news), datetime(2026, 8, 23, 17, 47))

    def test_parses_legacy_iso_date(self):
        news = SimpleNamespace(
            pub_date="2026-08-23 17:47:00",
            created_at=None,
        )

        self.assertEqual(parse_news_date(news), datetime(2026, 8, 23, 17, 47))

    def test_parses_rfc_822_date(self):
        news = SimpleNamespace(
            pub_date="Sun, 23 Aug 2026 17:47:00 +0900",
            created_at=None,
        )

        self.assertEqual(parse_news_date(news), datetime(2026, 8, 23, 17, 47))


if __name__ == "__main__":
    unittest.main()
