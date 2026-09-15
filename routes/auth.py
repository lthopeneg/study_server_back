import re
from datetime import datetime, timedelta
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

def is_valid_password(password):
    return isinstance(password, str) and re.match(r'^(?=.*[a-zA-Z])(?=.*\d)(?=.*[\W_]).{8,}$', password) is not None

def normalize_email(email):
    if not isinstance(email, str):
        return None
    try:
        return validate_email(email.strip(), check_deliverability=False).normalized
    except EmailNotValidError:
        return None

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
    requests = PendingSignup.query.filter_by(status='pending').order_by(PendingSignup.requested_at.asc()).all()
    return jsonify({"status": "success", "data": [{
        "id": item.id, "login_id": item.login_id, "email": item.email,
        "phone": item.phone, "requested_at": item.requested_at.isoformat() if item.requested_at else None,
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
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"status": "error", "message": "이미 사용 중인 아이디 또는 이메일입니다."}), 409
    return jsonify({"status": "success", "message": "가입 신청을 승인했습니다."})


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
    db.session.commit()
    return jsonify({"status": "success", "message": "가입 신청을 거절했습니다."})

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
