import unittest
from unittest.mock import MagicMock

from sqlalchemy.exc import OperationalError

from news_persistence import save_daily_main_news


class FakeArticle:
    query = None

    def __init__(self, **values):
        self.__dict__.update(values)


class NewsPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        FakeArticle.query = MagicMock()
        self.article_data = {
            "title": "테스트 기사",
            "content_md": "본문",
            "original_url": "https://example.com/news/1",
            "selection_reason": "선정 이유",
        }

    def test_retries_with_fresh_session_after_connection_error(self):
        FakeArticle.query.filter_by.return_value.first.side_effect = [None, None]
        self.db.session.commit.side_effect = [
            OperationalError("INSERT", {}, ConnectionResetError()),
            None,
        ]

        article, created = save_daily_main_news(
            self.db, FakeArticle, self.article_data, log=lambda _: None
        )

        self.assertTrue(created)
        self.assertEqual(article.original_url, self.article_data["original_url"])
        self.assertEqual(self.db.session.add.call_count, 2)
        self.assertEqual(self.db.session.commit.call_count, 2)
        self.db.session.rollback.assert_called_once()
        self.db.session.remove.assert_called_once()

    def test_retry_detects_ambiguous_success_without_duplicate_insert(self):
        stored_article = FakeArticle(**self.article_data)
        FakeArticle.query.filter_by.return_value.first.side_effect = [None, stored_article]
        self.db.session.commit.side_effect = OperationalError(
            "INSERT", {}, ConnectionResetError()
        )

        article, created = save_daily_main_news(
            self.db, FakeArticle, self.article_data, log=lambda _: None
        )

        self.assertFalse(created)
        self.assertIs(article, stored_article)
        self.db.session.add.assert_called_once()
        self.db.session.commit.assert_called_once()

    def test_non_connection_error_is_not_retried(self):
        FakeArticle.query.filter_by.return_value.first.return_value = None
        self.db.session.commit.side_effect = ValueError("invalid data")

        with self.assertRaises(ValueError):
            save_daily_main_news(
                self.db, FakeArticle, self.article_data, log=lambda _: None
            )

        self.db.session.add.assert_called_once()
        self.db.session.commit.assert_called_once()
        self.db.session.rollback.assert_called_once()
        self.db.session.remove.assert_called_once()


if __name__ == "__main__":
    unittest.main()
