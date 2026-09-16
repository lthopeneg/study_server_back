"""Append-only security audit records with data minimization."""
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

from flask import current_app, g, has_request_context, request
from flask_limiter.util import get_remote_address

from extensions import db
from models import AuditLog

AUDIT_RETENTION_DAYS = 180
FORBIDDEN_DETAIL_KEYS = {'password', 'token', 'secret', 'answer', 'content', 'csrf'}


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _safe_details(details):
    cleaned = {}
    for key, value in (details or {}).items():
        normalized = str(key).casefold()
        if any(blocked in normalized for blocked in FORBIDDEN_DETAIL_KEYS):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            cleaned[str(key)[:50]] = value if not isinstance(value, str) else value[:200]
        elif isinstance(value, list):
            cleaned[str(key)[:50]] = [item for item in value[:100] if isinstance(item, (str, int, float, bool))]
    return cleaned


def request_ip_hash():
    if not has_request_context():
        return None
    key = current_app.config['JWT_SECRET_KEY'].encode('utf-8')
    return hmac.new(key, get_remote_address().encode('utf-8'), hashlib.sha256).hexdigest()[:24]


def record_audit_event(event_type, *, actor=None, actor_login_id=None, target_type=None,
                       target_id=None, outcome='success', details=None):
    """Persist an audit event after the business transaction has completed."""
    try:
        cutoff = _utc_now() - timedelta(days=AUDIT_RETENTION_DAYS)
        AuditLog.query.filter(AuditLog.created_at < cutoff).delete(synchronize_session=False)
        entry = AuditLog(
            actor_user_id=actor.id if actor else None,
            actor_login_id=(actor.login_id if actor else actor_login_id)[:50] if (actor or actor_login_id) else None,
            event_type=event_type[:80], target_type=target_type[:50] if target_type else None,
            target_id=str(target_id)[:100] if target_id is not None else None,
            outcome=outcome[:20], request_id=getattr(g, 'request_id', None) if has_request_context() else None,
            ip_hash=request_ip_hash(),
            details_json=json.dumps(_safe_details(details), ensure_ascii=False, separators=(',', ':')) or None,
        )
        db.session.add(entry); db.session.commit()
        return entry
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Audit event persistence failed: %s', event_type)
        return None
