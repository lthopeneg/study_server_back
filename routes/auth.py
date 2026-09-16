import re
from datetime import datetime, timedelta
from html import escape
from flask import Blueprint, jsonify, request, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Message
from email_validator import validate_email, EmailNotValidError
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from flask_jwt_extended import (
    create_access_token, set_access_cookies, 
    jwt_required, get_jwt_identity, unset_jwt_cookies
)
from extensions import db, mail, limiter
from models import User, PendingSignup

# '/api' 로 시작하는 주소 묶음 선언
auth_bp = Blueprint('auth', __name__, url_prefix='/api')
SIGNUP_EXPIRY_DAYS = 7
SIGNUP_HISTORY_DAYS = 90

def is_valid_password(password):
    return isinstance(password, str) and re.match(r'^(?=.*[a-zA-Z])(?=.*\d)(?=.*[\W_]).{8,}$', password) is not None

def normalize_email(email):
    if not isinstance(email, str):
        return None
    try:
        return validate_email(email.strip(), check_deliverability=False).normalized
    except EmailNotValidError:
        return None

def build_email(title, greeting, body, action_label=None, action_url=None, accent='#2563eb'):
    action = ''
    if action_label and action_url:
        action = f'<a href="{escape(action_url)}" style="display:inline-block;margin-top:24px;padding:12px 22px;border-radius:9px;background:{accent};color:#fff;text-decoration:none;font-weight:700">{escape(action_label)}</a>'
    return f'''<!doctype html><html><body style="margin:0;background:#f1f5f9;font-family:Arial,sans-serif;color:#1e293b"><div style="max-width:580px;margin:32px auto;padding:0 16px"><div style="background:#0f172a;padding:22px 28px;border-radius:14px 14px 0 0;color:#fff"><div style="font-size:12px;letter-spacing:1.8px;color:#7dd3fc">SECURECODE SPACE</div><h1 style="margin:8px 0 0;font-size:24px">{escape(title)}</h1></div><div style="background:#fff;padding:30px 28px;border-radius:0 0 14px 14px;box-shadow:0 10px 30px rgba(15,23,42,.08)"><p style="font-size:17px;font-weight:700">{escape(greeting)}</p><div style="font-size:15px;line-height:1.7;color:#475569">{body}</div>{action}<p style="margin-top:30px;padding-top:18px;border-top:1px solid #e2e8f0;font-size:12px;color:#94a3b8">본 메일은 회원가입 신청 처리 결과를 안내하기 위해 발송되었습니다.</p></div></div></body></html>'''

def expire_and_prune_signup_requests():
    now = datetime.now()
    PendingSignup.query.filter(
        PendingSignup.status == 'pending',
        PendingSignup.requested_at < now - timedelta(days=SIGNUP_EXPIRY_DAYS),
    ).update({PendingSignup.status: 'expired', PendingSignup.decided_at: now, PendingSignup.password_hash: ''}, synchronize_session=False)
    PendingSignup.query.filter(
        PendingSignup.status != 'pending',
        PendingSignup.decided_at < now - timedelta(days=SIGNUP_HISTORY_DAYS),
    ).delete(synchronize_session=False)
    db.session.commit()

def send_decision_email(pending, approved):
    login_url = f"{current_app.config['PUBLIC_FRONTEND_URL']}/login"
    title = '회원가입이 승인되었습니다' if approved else '회원가입 신청 결과 안내'
    body = (
        f'<p><strong>{escape(pending.login_id)}</strong>님의 가입 신청이 승인되었습니다.</p><p>이제 등록한 아이디와 비밀번호로 로그인하여 학습을 시작할 수 있습니다.</p>'
        if approved else
        f'<p><strong>{escape(pending.login_id)}</strong>님의 가입 신청이 검토 후 승인되지 않았습니다.</p><p>문의가 필요하면 사이트 관리자에게 연락해 주세요.</p>'
    )
    message = Message(title, sender=current_app.config['MAIL_USERNAME'], recipients=[pending.email])
    message.body = f"{pending.login_id}님의 회원가입 신청이 {'승인되었습니다. 이제 로그인할 수 있습니다.' if approved else '승인되지 않았습니다.'}"
    message.html = build_email(title, '신청 처리 결과를 알려드립니다.', body, '로그인하기' if approved else None, login_url if approved else None, '#16a34a' if approved else '#64748b')
    mail.send(message)
    pending.notification_status = 'sent'
    pending.notification_sent_at = datetime.now()
    db.session.commit()

@auth_bp.route('/signup', methods=['POST'])
@limiter.limit("3 per hour")
def signup():
    data = request.get_json(silent=True) or {}
    login_id = data.get('login_id')
    password = data.get('password')
    email = normalize_email(data.get('email'))
    phone = data.get('phone')

    if not isinstance(login_id, str) or not login_id.strip() or not email:
        return jsonify({"status": "error", "message": "아이디와 올바른 이메일을 입력해주세요."}), 400

    login_id = login_id.strip()
    if User.query.filter_by(login_id=login_id).first():
        return jsonify({"status": "error", "message": "이미 사용 중인 아이디입니다."}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"status": "error", "message": "이미 가입된 이메일입니다."}), 400
    if not is_valid_password(password):
        return jsonify({"status": "error", "message": "비밀번호는 영문, 숫자, 특수문자를 포함해 8자리 이상이어야 합니다."}), 400

    expire_and_prune_signup_requests()
    pending = PendingSignup.query.filter(
        PendingSignup.status == 'pending',
        or_(PendingSignup.login_id == login_id, PendingSignup.email == email),
    ).first()
    if pending:
        return jsonify({"status": "error", "message": "이미 검토 중인 가입 신청이 있습니다."}), 409

    pending = PendingSignup(
        login_id=login_id,
        password_hash=generate_password_hash(password),
        email=email,
        phone=phone,
    )
    try:
        db.session.add(pending)
        db.session.commit()
        message = Message(
            "스터디 서버 회원가입 승인 요청",
            sender=current_app.config['MAIL_USERNAME'],
            recipients=[current_app.config['SIGNUP_APPROVAL_EMAIL']],
        )
        message.body = (
            f"새 회원가입 신청이 접수되었습니다.\n\n"
            f"아이디: {login_id}\n이메일: {email}\n\n"
            f"관리자 로그인 후 승인해 주세요:\n"
            f"{current_app.config['PUBLIC_FRONTEND_URL']}/admin/signup-requests"
        )
        message.html = build_email(
            '새 회원가입 승인 요청', '새로운 가입 신청이 도착했습니다.',
            f'<div style="margin:20px 0;padding:18px;background:#f8fafc;border-radius:10px"><div><strong>아이디</strong> · {escape(login_id)}</div><div style="margin-top:8px"><strong>이메일</strong> · {escape(email)}</div></div><p>신청은 {SIGNUP_EXPIRY_DAYS}일 동안 유효합니다. 관리자 로그인 후 승인 또는 거절해 주세요.</p>',
            '가입 신청 검토하기', f"{current_app.config['PUBLIC_FRONTEND_URL']}/admin/signup-requests"
        )
        mail.send(message)
    except Exception:
        db.session.rollback()
        if pending.id:
            PendingSignup.query.filter_by(id=pending.id, status='pending').delete(synchronize_session=False)
            db.session.commit()
        current_app.logger.exception("Signup approval request failed")
        return jsonify({"status": "error", "message": "가입 신청을 전달하지 못했습니다. 잠시 후 다시 시도해주세요."}), 500

    return jsonify({"status": "success", "message": "가입 신청이 접수되었습니다. 관리자 승인 후 로그인할 수 있습니다."}), 202


def get_admin_user():
    user = User.query.filter_by(login_id=get_jwt_identity()).first()
    return user if user and user.role == 'ADMIN' else None


@auth_bp.route('/admin/signup-requests', methods=['GET'])
@jwt_required()
def list_signup_requests():
    if not get_admin_user():
        return jsonify({"status": "error", "message": "접근 권한이 없습니다."}), 403
    expire_and_prune_signup_requests()
    requests = PendingSignup.query.order_by(PendingSignup.requested_at.desc()).limit(200).all()
    return jsonify({"status": "success", "data": [{
        "id": item.id, "login_id": item.login_id, "email": item.email,
        "phone": item.phone, "status": item.status,
        "requested_at": item.requested_at.isoformat() if item.requested_at else None,
        "decided_at": item.decided_at.isoformat() if item.decided_at else None,
        "notification_status": item.notification_status,
    } for item in requests]})


@auth_bp.route('/admin/signup-requests/<int:request_id>/approve', methods=['POST'])
@jwt_required()
def approve_signup_request(request_id):
    admin = get_admin_user()
    if not admin:
        return jsonify({"status": "error", "message": "접근 권한이 없습니다."}), 403
    pending = db.session.get(PendingSignup, request_id)
    if not pending or pending.status != 'pending':
        return jsonify({"status": "error", "message": "대기 중인 가입 신청을 찾을 수 없습니다."}), 404
    if User.query.filter(or_(User.login_id == pending.login_id, User.email == pending.email)).first():
        return jsonify({"status": "error", "message": "이미 사용 중인 아이디 또는 이메일입니다."}), 409
    db.session.add(User(login_id=pending.login_id, password=pending.password_hash, email=pending.email, phone=pending.phone))
    pending.status, pending.decided_at, pending.decided_by = 'approved', datetime.now(), admin.id
    pending.password_hash = ''
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"status": "error", "message": "이미 사용 중인 아이디 또는 이메일입니다."}), 409
    try:
        send_decision_email(pending, True)
        message = "가입 신청을 승인하고 결과 메일을 발송했습니다."
    except Exception:
        db.session.rollback()
        pending = db.session.get(PendingSignup, request_id)
        pending.notification_status = 'failed'
        db.session.commit()
        current_app.logger.exception("Signup approval notification failed")
        message = "가입 신청은 승인했지만 결과 메일을 발송하지 못했습니다."
    return jsonify({"status": "success", "message": message})


@auth_bp.route('/admin/signup-requests/<int:request_id>/reject', methods=['POST'])
@jwt_required()
def reject_signup_request(request_id):
    admin = get_admin_user()
    if not admin:
        return jsonify({"status": "error", "message": "접근 권한이 없습니다."}), 403
    pending = db.session.get(PendingSignup, request_id)
    if not pending or pending.status != 'pending':
        return jsonify({"status": "error", "message": "대기 중인 가입 신청을 찾을 수 없습니다."}), 404
    pending.status, pending.decided_at, pending.decided_by = 'rejected', datetime.now(), admin.id
    pending.password_hash = ''
    db.session.commit()
    try:
        send_decision_email(pending, False)
        message = "가입 신청을 거절하고 결과 메일을 발송했습니다."
    except Exception:
        db.session.rollback()
        pending = db.session.get(PendingSignup, request_id)
        pending.notification_status = 'failed'
        db.session.commit()
        current_app.logger.exception("Signup rejection notification failed")
        message = "가입 신청은 거절했지만 결과 메일을 발송하지 못했습니다."
    return jsonify({"status": "success", "message": message})

@auth_bp.route('/login', methods=['POST'])
@limiter.limit("5 per minute;20 per hour")
def login():
    data = request.json
    req_user_id = data.get('userId')
    req_password = data.get('password')
    user = User.query.filter_by(login_id=req_user_id).first()
    
    if user and check_password_hash(user.password, req_password):
        access_token = create_access_token(identity=user.login_id, expires_delta=timedelta(minutes=30))
        expires_at = int((datetime.now() + timedelta(minutes=30)).timestamp() * 1000)
        
        resp = jsonify({"status": "success", "username": user.login_id, "expires_at": expires_at, "message": f"{user.login_id}님 환영합니다!"})
        set_access_cookies(resp, access_token)
        return resp, 200
    return jsonify({"status": "error", "message": "아이디 또는 비밀번호가 잘못되었습니다."}), 401

@auth_bp.route('/check-auth', methods=['GET'])
@jwt_required()
def check_auth():
    current_user = get_jwt_identity()
    new_access_token = create_access_token(identity=current_user, expires_delta=timedelta(minutes=30))
    expires_at = int((datetime.now() + timedelta(minutes=30)).timestamp() * 1000)
    
    resp = jsonify({"status": "success", "username": current_user, "expires_at": expires_at})
    set_access_cookies(resp, new_access_token)
    return resp, 200

@auth_bp.route('/refresh', methods=['POST'])
@jwt_required()
def refresh():
    current_user = get_jwt_identity()
    new_access_token = create_access_token(identity=current_user, expires_delta=timedelta(minutes=30))
    expires_at = int((datetime.now() + timedelta(minutes=30)).timestamp() * 1000)
    
    resp = jsonify({"status": "success", "message": "세션이 30분 연장되었습니다.", "expires_at": expires_at})
    set_access_cookies(resp, new_access_token)
    return resp, 200

@auth_bp.route('/logout', methods=['POST'])
@jwt_required(optional=True)
def logout():
    resp = jsonify({"status": "success", "message": "안전하게 로그아웃 되었습니다."})
    unset_jwt_cookies(resp)
    return resp, 200
