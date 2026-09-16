"""Rate-limit keys that avoid storing raw account identifiers."""
import hashlib

from flask import jsonify, request
from flask_limiter.errors import RateLimitExceeded


def _hashed_json_value(field):
    data = request.get_json(silent=True) or {}
    value = data.get(field)
    normalized = value.strip().casefold() if isinstance(value, str) else '<missing>'
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:32]


def login_account_key():
    return f"login-account:{_hashed_json_value('userId')}"


def signup_email_key():
    return f"signup-email:{_hashed_json_value('email')}"


def install_rate_limit_error_handler(app):
    @app.errorhandler(RateLimitExceeded)
    def handle_rate_limit(_error):
        return jsonify({
            'status': 'error',
            'message': '요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.',
        }), 429
