import unittest

from flask import Flask
from flask_limiter import Limiter

from rate_limit_config import install_rate_limit_error_handler, login_account_key, signup_email_key


class RateLimitKeyTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def test_login_key_is_normalized_and_does_not_expose_identifier(self):
        with self.app.test_request_context(json={'userId': ' Admin '}):
            first = login_account_key()
        with self.app.test_request_context(json={'userId': 'admin'}):
            second = login_account_key()
        self.assertEqual(first, second)
        self.assertNotIn('admin', first)

    def test_signup_key_does_not_expose_email(self):
        with self.app.test_request_context(json={'email': 'Person@Example.com'}):
            key = signup_email_key()
        self.assertNotIn('person@example.com', key)

    def test_missing_identifier_uses_stable_bucket(self):
        with self.app.test_request_context(json={}):
            first = login_account_key()
        with self.app.test_request_context(data='invalid', content_type='application/json'):
            second = login_account_key()
        self.assertEqual(first, second)

    def test_rate_limit_returns_consistent_json(self):
        limiter = Limiter(key_func=lambda: 'client', storage_uri='memory://')
        limiter.init_app(self.app)
        install_rate_limit_error_handler(self.app)

        @self.app.get('/limited')
        @limiter.limit('1 per minute')
        def limited():
            return {'status': 'success'}

        client = self.app.test_client()
        self.assertEqual(client.get('/limited').status_code, 200)
        rejected = client.get('/limited')
        self.assertEqual(rejected.status_code, 429)
        self.assertEqual(rejected.get_json()['status'], 'error')
        self.assertIn('잠시 후', rejected.get_json()['message'])


if __name__ == '__main__':
    unittest.main()
