"""JWT session-version validation and consistent invalidation responses."""
from flask import jsonify

from extensions import db
from models import User


def session_claims(user):
    return {'session_version': user.session_version or 0}


def invalidate_user_sessions(user):
    user.session_version = (user.session_version or 0) + 1


def install_session_security(jwt_manager):
    @jwt_manager.token_in_blocklist_loader
    def token_has_stale_session_version(_jwt_header, jwt_payload):
        user = db.session.execute(
            db.select(User).filter_by(login_id=jwt_payload.get('sub'))
        ).scalar_one_or_none()
        token_version = jwt_payload.get('session_version', 0)
        return user is None or token_version != (user.session_version or 0)

    @jwt_manager.revoked_token_loader
    def revoked_token_response(_jwt_header, _jwt_payload):
        return jsonify({
            'status': 'error',
            'code': 'SESSION_INVALIDATED',
            'message': '계정 보안 정보가 변경되어 다시 로그인해야 합니다.',
        }), 401

    @jwt_manager.expired_token_loader
    def expired_token_response(_jwt_header, _jwt_payload):
        return jsonify({
            'status': 'error',
            'code': 'SESSION_EXPIRED',
            'message': '로그인 세션이 만료되었습니다.',
        }), 401
