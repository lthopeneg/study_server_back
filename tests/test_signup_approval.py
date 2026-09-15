import unittest
from unittest.mock import patch

from flask import Flask
from flask_jwt_extended import create_access_token
from werkzeug.security import check_password_hash

from extensions import db, jwt, limiter
from models import PendingSignup, User
from routes.auth import auth_bp


class SignupApprovalTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI='sqlite://', SQLALCHEMY_TRACK_MODIFICATIONS=False,
            JWT_SECRET_KEY='test-secret-for-signup-approval-32-bytes', RATELIMIT_ENABLED=False,
            MAIL_USERNAME='sender@example.com', SIGNUP_APPROVAL_EMAIL='admin@example.com',
            PUBLIC_FRONTEND_URL='https://scspace.duckdns.org',
        )
        db.init_app(self.app); jwt.init_app(self.app); limiter.init_app(self.app)
        self.app.register_blueprint(auth_bp)
        self.context = self.app.app_context(); self.context.push(); db.create_all()
        db.session.add_all([
            User(id=1, login_id='admin', password='hash', email='admin@example.com', role='ADMIN'),
            User(id=2, login_id='user', password='hash', email='user@example.com', role='USER'),
        ])
        db.session.commit(); self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.context.pop()

    def headers(self, identity):
        return {'Authorization': f'Bearer {create_access_token(identity=identity)}'}

    @patch('routes.auth.mail.send')
    def test_signup_stays_pending_until_admin_approves(self, send_mail):
        response = self.client.post('/api/signup', json={
            'login_id': 'learner', 'password': 'Password!1',
            'email': 'learner@example.com', 'phone': '010-0000-0000',
        })
        self.assertEqual(response.status_code, 202)
        self.assertIsNone(User.query.filter_by(login_id='learner').first())
        pending = PendingSignup.query.filter_by(login_id='learner').one()
        self.assertTrue(check_password_hash(pending.password_hash, 'Password!1'))
        self.assertNotIn('Password!1', send_mail.call_args.args[0].body)

        approved = self.client.post(
            f'/api/admin/signup-requests/{pending.id}/approve', headers=self.headers('admin'),
        )
        self.assertEqual(approved.status_code, 200, approved.get_json())
        self.assertIsNotNone(User.query.filter_by(login_id='learner').first())
        self.assertEqual(pending.status, 'approved')

    @patch('routes.auth.mail.send')
    def test_non_admin_cannot_approve(self, _send_mail):
        self.client.post('/api/signup', json={
            'login_id': 'learner', 'password': 'Password!1', 'email': 'learner@example.com', 'phone': '010',
        })
        pending = PendingSignup.query.filter_by(login_id='learner').one()
        response = self.client.post(
            f'/api/admin/signup-requests/{pending.id}/approve', headers=self.headers('user'),
        )
        self.assertEqual(response.status_code, 403)


if __name__ == '__main__':
    unittest.main()
