import unittest

from flask import Flask, jsonify
from flask_jwt_extended import create_access_token, jwt_required, set_access_cookies

from extensions import jwt
from security_config import install_security_headers, parse_boolean_setting, parse_cors_origins


class SecurityConfigTestCase(unittest.TestCase):
    def test_applies_api_security_headers(self):
        app = Flask(__name__)
        install_security_headers(app)

        @app.get('/api/profile')
        def profile():
            return jsonify(status='success')

        response = app.test_client().get('/api/profile')
        self.assertEqual(response.headers['Strict-Transport-Security'], 'max-age=31536000')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIn('camera=()', response.headers['Permissions-Policy'])

    def test_parses_explicit_boolean_values(self):
        self.assertTrue(parse_boolean_setting('true'))
        self.assertTrue(parse_boolean_setting(' ON '))
        self.assertFalse(parse_boolean_setting('false', True))

    def test_rejects_invalid_boolean_value(self):
        with self.assertRaises(ValueError):
            parse_boolean_setting('sometimes')

    def test_uses_local_frontend_as_default_cors_origin(self):
        self.assertEqual(parse_cors_origins(None), ['http://localhost:5173'])

    def test_normalizes_configured_cors_origins(self):
        self.assertEqual(
            parse_cors_origins('https://scspace.duckdns.org/, http://localhost:5173'),
            ['https://scspace.duckdns.org', 'http://localhost:5173'],
        )

    def test_rejects_wildcard_cors_with_credentials(self):
        with self.assertRaises(ValueError):
            parse_cors_origins('*')

    def test_cookie_authenticated_post_requires_csrf_header(self):
        app = Flask(__name__)
        app.config.update(
            JWT_SECRET_KEY='test-secret-for-security-config-32-bytes',
            JWT_TOKEN_LOCATION=['cookies'],
            JWT_COOKIE_SECURE=True,
            JWT_COOKIE_SAMESITE='Lax',
            JWT_COOKIE_CSRF_PROTECT=True,
        )
        jwt.init_app(app)

        @app.post('/login')
        def login():
            response = jsonify(status='success')
            set_access_cookies(response, create_access_token(identity='learner'))
            return response

        @app.post('/protected')
        @jwt_required()
        def protected():
            return jsonify(status='success')

        client = app.test_client()
        login_response = client.post('/login')
        cookies = login_response.headers.getlist('Set-Cookie')
        self.assertTrue(any('Secure' in value and 'HttpOnly' in value and 'SameSite=Lax' in value for value in cookies))

        rejected = client.post('/protected')
        self.assertEqual(rejected.status_code, 401)

        csrf_cookie = client.get_cookie('csrf_access_token')
        accepted = client.post('/protected', headers={'X-CSRF-TOKEN': csrf_cookie.value})
        self.assertEqual(accepted.status_code, 200)


if __name__ == '__main__':
    unittest.main()
