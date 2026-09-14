import unittest
from datetime import datetime

from flask import Flask
from flask_jwt_extended import create_access_token

from extensions import db, jwt, limiter
from models import DailyMainNews, SecurityNews, User, UserNewsBookmark
from routes.user import user_bp


class NewsBookmarkApiTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI='sqlite://',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            JWT_SECRET_KEY='test-secret-for-news-bookmark-api-tests',
        )
        db.init_app(self.app)
        jwt.init_app(self.app)
        limiter.init_app(self.app)
        self.app.register_blueprint(user_bp)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        db.session.add_all([
            User(id=1, login_id='learner', password='hash', email='learner@example.com', role='USER'),
            User(id=2, login_id='other', password='hash', email='other@example.com', role='USER'),
            SecurityNews(id=10, title='보안 뉴스', link='https://example.com/security', pub_date='2026-09-14', source='테스트 매체'),
            DailyMainNews(id=20, title='AI 뉴스', content_md='본문', original_url='https://example.com/ai', created_at=datetime(2026, 9, 14)),
        ])
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def headers(self, login_id='learner'):
        return {'Authorization': f'Bearer {create_access_token(identity=login_id)}'}

    def test_create_list_and_delete_bookmark(self):
        response = self.client.post('/api/user/news-bookmarks', json={
            'item_type': 'security_news', 'news_id': 10,
        }, headers=self.headers())
        self.assertEqual(response.status_code, 201)
        bookmark_id = response.get_json()['data']['id']

        duplicate = self.client.post('/api/user/news-bookmarks', json={
            'item_type': 'security_news', 'news_id': 10,
        }, headers=self.headers())
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(UserNewsBookmark.query.count(), 1)

        listing = self.client.get('/api/user/news-bookmarks', headers=self.headers())
        self.assertEqual(listing.get_json()['data'][0]['title'], '보안 뉴스')

        forbidden = self.client.delete(f'/api/user/news-bookmarks/{bookmark_id}', headers=self.headers('other'))
        self.assertEqual(forbidden.status_code, 404)
        deleted = self.client.delete(f'/api/user/news-bookmarks/{bookmark_id}', headers=self.headers())
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(UserNewsBookmark.query.count(), 0)

    def test_bookmarks_ai_news_using_server_snapshot(self):
        response = self.client.post('/api/user/news-bookmarks', json={
            'item_type': 'daily_main', 'news_id': 20,
        }, headers=self.headers())
        self.assertEqual(response.status_code, 201)
        data = response.get_json()['data']
        self.assertEqual(data['source'], 'AI 메인 뉴스')
        self.assertEqual(data['url'], 'https://example.com/ai')

    def test_rejects_unknown_news(self):
        response = self.client.post('/api/user/news-bookmarks', json={
            'item_type': 'security_news', 'news_id': 999,
        }, headers=self.headers())
        self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()
