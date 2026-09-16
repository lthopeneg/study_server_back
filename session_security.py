"""JWT session-version and per-device session validation."""
import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone

from flask import current_app, jsonify, request

from extensions import db
from models import User, UserSession
from audit import request_ip_hash


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _user_agent_hash():
    value = request.headers.get('User-Agent', '')[:500]
    if not value:
        return None
    key = current_app.config['JWT_SECRET_KEY'].encode('utf-8')
    return hmac.new(key, value.encode('utf-8'), hashlib.sha256).hexdigest()[:24]


def create_user_session(user, lifetime=timedelta(minutes=30)):
    now = _utc_now()
    UserSession.query.filter(
        UserSession.expires_at < now - timedelta(days=30)
    ).delete(synchronize_session=False)
    session = UserSession(
        session_id=str(uuid.uuid4()), user_id=user.id,
        ip_hash=request_ip_hash(), user_agent_hash=_user_agent_hash(),
        created_at=now, last_seen_at=now, expires_at=now + lifetime,
    )
    db.session.add(session)
    db.session.commit()
    return session


def session_claims(user, session=None):
    claims = {'session_version': user.session_version or 0}
    if session:
        claims['session_id'] = session.session_id
    return claims


def extend_user_session(session, lifetime=timedelta(minutes=30)):
    now = _utc_now()
    session.last_seen_at = now
    session.expires_at = now + lifetime
    db.session.commit()


def revoke_user_session(session):
    if session and not session.revoked_at:
        session.revoked_at = _utc_now()
        db.session.commit()


def invalidate_user_sessions(user):
    user.session_version = (user.session_version or 0) + 1
    UserSession.query.filter_by(user_id=user.id, revoked_at=None).update(
        {'revoked_at': _utc_now()}, synchronize_session=False,
    )


def install_session_security(jwt_manager):
    @jwt_manager.token_in_blocklist_loader
    def token_has_stale_session_version(_jwt_header, jwt_payload):
        user = db.session.execute(
            db.select(User).filter_by(login_id=jwt_payload.get('sub'))
        ).scalar_one_or_none()
        token_version = jwt_payload.get('session_version', 0)
        if user is None or token_version != (user.session_version or 0):
            return True
        session_id = jwt_payload.get('session_id')
        if not session_id:
            return False
        session = UserSession.query.filter_by(session_id=session_id, user_id=user.id).first()
        return not session or session.revoked_at is not None or session.expires_at <= _utc_now()

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
