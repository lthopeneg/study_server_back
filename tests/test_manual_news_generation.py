import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from flask_jwt_extended import create_access_token

from extensions import db, jwt, limiter
from models import DailyMainNews, SecurityNews, User, UserNewsBookmark
from routes.news import get_ai_article_ids_by_url, news_bp
from services.news_ai import NewsSourceValidationError, validate_public_news_url


class ManualNewsGenerationApiTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI='sqlite://',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            JWT_SECRET_KEY='test-secret-for-manual-news-generation',
            RATELIMIT_ENABLED=False,
        )
        db.init_app(self.app)
        jwt.init_app(self.app)
        limiter.init_app(self.app)
        self.app.register_blueprint(news_bp)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        db.session.add_all([
            User(id=1, login_id='admin', password='hash', email='admin@example.com', role='ADMIN'),
            User(id=2, login_id='user', password='hash', email='user@example.com', role='USER'),
            SecurityNews(id=10, title='선택 뉴스', link='https://example.com/news', source='테스트'),
        ])
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def headers(self, identity):
        return {'Authorization': f'Bearer {create_access_token(identity=identity)}'}

    def test_rejects_non_admin(self):
        response = self.client.post('/api/news/10/generate-ai-article', headers=self.headers('user'))
        self.assertEqual(response.status_code, 403)

    @patch('routes.news.save_daily_main_news')
    @patch('routes.news.write_news_article', return_value='생성된 기사')
    def test_admin_generates_selected_article(self, write_article, save_article):
        save_article.return_value = (SimpleNamespace(id=30), True)
        response = self.client.post('/api/news/10/generate-ai-article', headers=self.headers('admin'))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()['data']['id'], 30)
        write_article.assert_called_once_with('선택 뉴스', 'https://example.com/news')
        saved = save_article.call_args.args[2]
        self.assertEqual(saved['original_url'], 'https://example.com/news')

    def test_rejects_duplicate_source_url_before_ai_call(self):
        db.session.add(DailyMainNews(
            id=20, title='기존 기사', content_md='본문',
            original_url='https://example.com/news', selection_reason='기존',
        ))
        db.session.commit()
        with patch('routes.news.write_news_article') as write_article:
            response = self.client.post('/api/news/10/generate-ai-article', headers=self.headers('admin'))
        self.assertEqual(response.status_code, 409)
        write_article.assert_not_called()

    @patch('routes.news.write_news_article', side_effect=NewsSourceValidationError('HTML 형식의 기사 원문만 가져올 수 있습니다.'))
    def test_expected_source_validation_keeps_safe_guidance(self, _write_article):
        response = self.client.post('/api/news/10/generate-ai-article', headers=self.headers('admin'))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json['message'], 'HTML 형식의 기사 원문만 가져올 수 있습니다.')

    @patch('routes.news.write_news_article', side_effect=ValueError('private-path=/etc/service/config'))
    def test_unexpected_validation_does_not_expose_exception(self, _write_article):
        response = self.client.post('/api/news/10/generate-ai-article', headers=self.headers('admin'))
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('private-path', response.json['message'])

    @patch('routes.news.write_news_article', side_effect=RuntimeError('upstream key=secret-value'))
    def test_upstream_failure_does_not_expose_exception(self, _write_article):
        response = self.client.post('/api/news/10/generate-ai-article', headers=self.headers('admin'))
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('secret-value', response.json['message'])

    def test_maps_news_to_existing_ai_article(self):
        db.session.add_all([
            DailyMainNews(
                id=20, title='기존 기사', content_md='본문',
                original_url='https://example.com/news', selection_reason='기존',
            ),
            DailyMainNews(
                id=21, title='최신 기사', content_md='본문',
                original_url='https://example.com/news', selection_reason='최신',
            ),
        ])
        db.session.commit()

        news = db.session.get(SecurityNews, 10)
        self.assertEqual(get_ai_article_ids_by_url([news]), {'https://example.com/news': 21})

    def test_admin_deletes_ai_article_and_its_direct_bookmarks(self):
        db.session.add_all([
            DailyMainNews(
                id=20, title='삭제할 기사', content_md='본문',
                original_url='https://example.com/news', selection_reason='기존',
            ),
            UserNewsBookmark(
                id=30, user_id=2, item_type='daily_main', news_id=20,
                title='삭제할 기사', url='https://example.com/news', source='AI 메인 뉴스',
            ),
        ])
        db.session.commit()

        response = self.client.delete('/api/news/daily-main/20', headers=self.headers('admin'))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(db.session.get(DailyMainNews, 20))
        self.assertIsNone(db.session.get(UserNewsBookmark, 30))

    def test_non_admin_cannot_delete_ai_article(self):
        db.session.add(DailyMainNews(
            id=20, title='보호된 기사', content_md='본문',
            original_url='https://example.com/news', selection_reason='기존',
        ))
        db.session.commit()

        response = self.client.delete('/api/news/daily-main/20', headers=self.headers('user'))

        self.assertEqual(response.status_code, 403)
        self.assertIsNotNone(db.session.get(DailyMainNews, 20))


class NewsUrlValidationTestCase(unittest.TestCase):
    @patch('services.news_ai.socket.getaddrinfo', return_value=[(None, None, None, None, ('127.0.0.1', 80))])
    def test_rejects_private_or_loopback_destination(self, _resolver):
        with self.assertRaisesRegex(ValueError, '공개 인터넷 주소'):
            validate_public_news_url('http://localhost/article')


if __name__ == '__main__':
    unittest.main()
