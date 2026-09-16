import json
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from models import AuditLog, User

audit_bp = Blueprint('audit', __name__, url_prefix='/api/admin/audit-logs')


@audit_bp.get('')
@jwt_required()
def get_audit_logs():
    admin = User.query.filter_by(login_id=get_jwt_identity()).first()
    if not admin or admin.role != 'ADMIN':
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
