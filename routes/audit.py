import json
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from audit import record_audit_event
from extensions import db
from models import AuditLog, SecurityAlert, User

audit_bp = Blueprint('audit', __name__, url_prefix='/api/admin/audit-logs')
security_alert_bp = Blueprint('security_alert', __name__, url_prefix='/api/admin/security-alerts')


def _admin_user():
    user = User.query.filter_by(login_id=get_jwt_identity()).first()
    return user if user and user.role == 'ADMIN' else None


@audit_bp.get('')
@jwt_required()
def get_audit_logs():
    admin = _admin_user()
    if not admin:
        return jsonify({'status': 'error', 'message': '접근 권한이 없습니다.'}), 403
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 30, type=int)
    if page < 1 or limit < 1 or limit > 100:
        return jsonify({'status': 'error', 'message': '페이지 범위가 올바르지 않습니다.'}), 400
    query = AuditLog.query
    event_type, outcome, actor = request.args.get('event_type'), request.args.get('outcome'), request.args.get('actor')
    if event_type: query = query.filter(AuditLog.event_type == event_type[:80])
    if outcome in {'success', 'failure'}: query = query.filter(AuditLog.outcome == outcome)
    if actor: query = query.filter(AuditLog.actor_login_id.contains(actor[:50]))
    total = query.count()
    items = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).offset((page - 1) * limit).limit(limit).all()
    return jsonify({'status': 'success', 'data': [{
        'id': item.id, 'actor_login_id': item.actor_login_id, 'event_type': item.event_type,
        'target_type': item.target_type, 'target_id': item.target_id, 'outcome': item.outcome,
        'request_id': item.request_id, 'ip_hash': item.ip_hash,
        'details': json.loads(item.details_json) if item.details_json else {},
        'created_at': item.created_at.replace(tzinfo=timezone.utc).isoformat() if item.created_at else None,
    } for item in items], 'total': total, 'page': page, 'total_pages': (total + limit - 1) // limit})


@security_alert_bp.get('')
@jwt_required()
def get_security_alerts():
    if not _admin_user():
        return jsonify({'status': 'error', 'message': '접근 권한이 없습니다.'}), 403
    status = request.args.get('status', 'open')
    if status not in {'open', 'resolved', 'all'}:
        return jsonify({'status': 'error', 'message': '상태 값이 올바르지 않습니다.'}), 400
    query = SecurityAlert.query
    if status != 'all':
        query = query.filter_by(status=status)
    alerts = query.order_by(SecurityAlert.last_seen_at.desc(), SecurityAlert.id.desc()).limit(200).all()
    return jsonify({'status': 'success', 'data': [{
        'id': alert.id, 'category': alert.category, 'severity': alert.severity,
        'title': alert.title, 'description': alert.description,
        'actor_login_id': alert.actor_login_id, 'ip_hash': alert.ip_hash,
        'status': alert.status, 'occurrence_count': alert.occurrence_count,
        'first_seen_at': alert.first_seen_at.replace(tzinfo=timezone.utc).isoformat(),
        'last_seen_at': alert.last_seen_at.replace(tzinfo=timezone.utc).isoformat(),
        'notified_at': alert.notified_at.replace(tzinfo=timezone.utc).isoformat() if alert.notified_at else None,
        'resolved_at': alert.resolved_at.replace(tzinfo=timezone.utc).isoformat() if alert.resolved_at else None,
    } for alert in alerts]})


@security_alert_bp.post('/<int:alert_id>/resolve')
@jwt_required()
def resolve_security_alert(alert_id):
    admin = _admin_user()
    if not admin:
        return jsonify({'status': 'error', 'message': '접근 권한이 없습니다.'}), 403
    alert = db.session.get(SecurityAlert, alert_id)
    if not alert:
        return jsonify({'status': 'error', 'message': '보안 알림을 찾을 수 없습니다.'}), 404
    if alert.status != 'resolved':
        alert.status = 'resolved'
        alert.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        alert.resolved_by = admin.id
        db.session.commit()
        record_audit_event('security_alert.resolve', actor=admin, target_type='security_alert', target_id=alert.id)
    return jsonify({'status': 'success', 'message': '보안 알림을 확인 완료로 처리했습니다.'})
