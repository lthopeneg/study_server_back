import unittest
from datetime import datetime, timedelta

from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token

from extensions import db, limiter
from models import User, UserSession
from routes.auth import auth_bp
from session_security import install_session_security, session_claims


class LoginSessionTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI='sqlite://', SQLALCHEMY_TRACK_MODIFICATIONS=False,
            JWT_SECRET_KEY='test-secret-for-login-sessions-32-bytes', RATELIMIT_ENABLED=False,
        )
        db.init_app(self.app); self.jwt=JWTManager(self.app); limiter.init_app(self.app)
        install_session_security(self.jwt); self.app.register_blueprint(auth_bp)
        self.context=self.app.app_context(); self.context.push(); db.create_all()
        self.user=User(login_id='learner',password='hash',email='learner@example.com',role='USER')
        db.session.add(self.user); db.session.commit()
        now=datetime.now()
        self.current=UserSession(session_id='current-session',user_id=self.user.id,created_at=now,last_seen_at=now,expires_at=now+timedelta(minutes=30))
        self.other=UserSession(session_id='other-session',user_id=self.user.id,created_at=now,last_seen_at=now,expires_at=now+timedelta(minutes=30))
        db.session.add_all([self.current,self.other]); db.session.commit(); self.client=self.app.test_client()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.context.pop()

    def headers(self):
        token=create_access_token(identity=self.user.login_id,additional_claims=session_claims(self.user,self.current))
        return {'Authorization':f'Bearer {token}'}

    def test_lists_and_revokes_owned_session(self):
        listed=self.client.get('/api/sessions',headers=self.headers())
        self.assertEqual(listed.status_code,200)
        self.assertEqual(len(listed.get_json()['data']),2)
        self.assertEqual(sum(item['is_current'] for item in listed.get_json()['data']),1)
        revoked=self.client.delete('/api/sessions/other-session',headers=self.headers())
        self.assertEqual(revoked.status_code,200)
        self.assertIsNotNone(db.session.get(UserSession,self.other.id).revoked_at)

    def test_cannot_revoke_another_users_session(self):
        another=User(login_id='other',password='hash',email='other@example.com',role='USER')
        db.session.add(another); db.session.flush()
        foreign=UserSession(session_id='foreign-session',user_id=another.id,expires_at=datetime.now()+timedelta(minutes=30))
        db.session.add(foreign); db.session.commit()
        response=self.client.delete('/api/sessions/foreign-session',headers=self.headers())
        self.assertEqual(response.status_code,404)
        self.assertIsNone(db.session.get(UserSession,foreign.id).revoked_at)


if __name__=='__main__':
    unittest.main()
