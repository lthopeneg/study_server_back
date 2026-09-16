"""Create actionable alerts from authentication audit events."""
from datetime import datetime, timedelta, timezone
from html import escape

from flask import current_app
from flask_mail import Message

from extensions import db, mail
from models import AuditLog, SecurityAlert

WINDOW_MINUTES = 15


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _send_alert_email(alert):
    recipient = current_app.config.get('SIGNUP_APPROVAL_EMAIL')
    sender = current_app.config.get('MAIL_USERNAME')
    if not recipient or not sender:
        return
    url = f"{current_app.config['PUBLIC_FRONTEND_URL']}/admin/audit-logs"
    message = Message(f'[보안 알림] {alert.title}', sender=sender, recipients=[recipient])
    message.body = f'{alert.title}\n{alert.description}\n관리자 확인: {url}'
    message.html = f'''<!doctype html><html><body style="margin:0;background:#f1f5f9;font-family:Arial,sans-serif;color:#172033"><div style="max-width:580px;margin:32px auto;padding:0 16px"><div style="background:#991b1b;padding:22px 28px;border-radius:14px 14px 0 0;color:#fff"><div style="font-size:12px;letter-spacing:1.6px;color:#fecaca">SECURECODE SPACE · SECURITY ALERT</div><h1 style="margin:8px 0 0;font-size:23px">{escape(alert.title)}</h1></div><div style="background:#fff;padding:28px;border-radius:0 0 14px 14px"><p style="line-height:1.7;color:#475569">{escape(alert.description)}</p><div style="margin:20px 0;padding:14px 16px;background:#fff7ed;border:1px solid #fed7aa;border-radius:10px"><strong>위험도</strong> · {escape(alert.severity.upper())}<br><strong>발생 계정</strong> · {escape(alert.actor_login_id or '확인 불가')}<br><strong>IP 식별값</strong> · {escape(alert.ip_hash or '확인 불가')}</div><a href="{escape(url)}" style="display:inline-block;padding:12px 20px;border-radius:9px;background:#dc2626;color:#fff;text-decoration:none;font-weight:700">보안 이벤트 확인</a><p style="margin-top:26px;font-size:12px;color:#94a3b8">IP 원문은 저장하지 않으며 동일 접속지를 구분하기 위한 요약값만 표시합니다.</p></div></div></body></html>'''
    mail.send(message)


def _upsert_alert(*, fingerprint, category, severity, title, description,
                  actor_login_id=None, ip_hash=None, notify=False):
    now = _utc_now()
    alert = SecurityAlert.query.filter_by(fingerprint=fingerprint, status='open').first()
    if alert:
        alert.occurrence_count += 1
        alert.last_seen_at = now
        alert.description = description[:500]
    else:
        alert = SecurityAlert(
            fingerprint=fingerprint[:160], category=category, severity=severity,
            title=title[:160], description=description[:500],
            actor_login_id=(actor_login_id or '')[:50] or None,
            ip_hash=ip_hash, first_seen_at=now, last_seen_at=now,
        )
        db.session.add(alert)
    db.session.commit()
    if notify and not alert.notified_at:
        try:
            _send_alert_email(alert)
            alert.notified_at = _utc_now()
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Security alert email failed: %s', alert.id)
    return alert


def evaluate_login_event(event, *, is_admin=False):
    """Evaluate a persisted auth.login audit event without storing raw IP data."""
    if not event:
        return
    cutoff = _utc_now() - timedelta(minutes=WINDOW_MINUTES)
    login_id = event.actor_login_id
    ip_hash = event.ip_hash

    if event.outcome == 'failure':
        account_failures = AuditLog.query.filter(
            AuditLog.event_type == 'auth.login', AuditLog.outcome == 'failure',
            AuditLog.actor_login_id == login_id, AuditLog.created_at >= cutoff,
        ).count()
        if is_admin:
            _upsert_alert(
                fingerprint=f'admin-failure:{login_id}:{ip_hash}', category='admin_login_failure',
                severity='high', title='관리자 로그인 실패',
                description='관리자 계정에 대한 로그인 실패가 감지되었습니다.',
                actor_login_id=login_id, ip_hash=ip_hash, notify=True,
            )
        elif login_id and account_failures >= 5:
            _upsert_alert(
                fingerprint=f'account-failures:{login_id}', category='repeated_login_failure',
                severity='medium', title='계정 로그인 반복 실패',
                description=f'{WINDOW_MINUTES}분 동안 같은 계정에서 {account_failures}회의 로그인 실패가 발생했습니다.',
                actor_login_id=login_id, ip_hash=ip_hash,
            )

        if ip_hash:
            targeted_accounts = db.session.query(AuditLog.actor_login_id).filter(
                AuditLog.event_type == 'auth.login', AuditLog.outcome == 'failure',
                AuditLog.ip_hash == ip_hash, AuditLog.created_at >= cutoff,
                AuditLog.actor_login_id.isnot(None),
            ).distinct().count()
            if targeted_accounts >= 3:
                _upsert_alert(
                    fingerprint=f'credential-scan:{ip_hash}', category='credential_scanning',
                    severity='high', title='다중 계정 로그인 공격 의심',
                    description=f'{WINDOW_MINUTES}분 동안 동일 접속지에서 {targeted_accounts}개 계정에 로그인을 시도했습니다.',
                    actor_login_id=login_id, ip_hash=ip_hash, notify=True,
                )
        return

    if is_admin and ip_hash:
        known_ip = AuditLog.query.filter(
            AuditLog.id != event.id, AuditLog.event_type == 'auth.login',
            AuditLog.outcome == 'success', AuditLog.actor_login_id == login_id,
            AuditLog.ip_hash == ip_hash,
        ).first()
        previous_success = AuditLog.query.filter(
            AuditLog.id != event.id, AuditLog.event_type == 'auth.login',
            AuditLog.outcome == 'success', AuditLog.actor_login_id == login_id,
        ).first()
        if previous_success and not known_ip:
            _upsert_alert(
                fingerprint=f'new-admin-ip:{login_id}:{ip_hash}', category='new_admin_location',
                severity='high', title='새 접속지의 관리자 로그인',
                description='이전에 확인되지 않은 접속지에서 관리자 로그인이 성공했습니다.',
                actor_login_id=login_id, ip_hash=ip_hash, notify=True,
            )
