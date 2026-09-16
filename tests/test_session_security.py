import unittest

from flask import Flask, jsonify
from flask_jwt_extended import JWTManager, create_access_token, jwt_required

from extensions import db
from models import User
from session_security import install_session_security, invalidate_user_sessions, session_claims


class SessionSecurityTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI='sqlite://',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            JWT_SECRET_KEY='test-secret-for-session-security-32-bytes',
            JWT_TOKEN_LOCATION=['headers'],
        )
        db.init_app(self.app)
        self.jwt = JWTManager(self.app)
        install_session_security(self.jwt)

        @self.app.get('/protected')
        @jwt_required()
        def protected():
            return jsonify(status='success')

        self.context = self.app.app_context(); self.context.push(); db.create_all()
        self.user = User(login_id='learner', password='hash', email='learner@example.com', role='USER')
        db.session.add(self.user); db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.context.pop()

    def token(self):
        return create_access_token(identity=self.user.login_id, additional_claims=session_claims(self.user))

    def test_old_token_is_rejected_after_session_version_changes(self):
        token = self.token()
        accepted = self.client.get('/protected', headers={'Authorization': f'Bearer {token}'})
        self.assertEqual(accepted.status_code, 200)

        invalidate_user_sessions(self.user); db.session.commit()
        rejected = self.client.get('/protected', headers={'Authorization': f'Bearer {token}'})
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(rejected.get_json()['code'], 'SESSION_INVALIDATED')

        replacement = self.token()
        accepted_again = self.client.get('/protected', headers={'Authorization': f'Bearer {replacement}'})
        self.assertEqual(accepted_again.status_code, 200)

    def test_legacy_token_is_valid_only_at_initial_version(self):
        token = create_access_token(identity=self.user.login_id)
        self.assertEqual(self.client.get('/protected', headers={'Authorization': f'Bearer {token}'}).status_code, 200)
        invalidate_user_sessions(self.user); db.session.commit()
        self.assertEqual(self.client.get('/protected', headers={'Authorization': f'Bearer {token}'}).status_code, 401)


if __name__ == '__main__':
    unittest.main()
