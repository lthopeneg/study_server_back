import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from flask_jwt_extended import create_access_token

from extensions import db, jwt, limiter
from models import DailyMainNews, SecurityNews, User
from routes.news import news_bp
from services.news_ai import validate_public_news_url


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


class NewsUrlValidationTestCase(unittest.TestCase):
    @patch('services.news_ai.socket.getaddrinfo', return_value=[(None, None, None, None, ('127.0.0.1', 80))])
    def test_rejects_private_or_loopback_destination(self, _resolver):
        with self.assertRaisesRegex(ValueError, '공개 인터넷 주소'):
            validate_public_news_url('http://localhost/article')


if __name__ == '__main__':
    unittest.main()
