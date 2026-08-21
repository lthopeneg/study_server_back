import re
import secrets
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Message
from email_validator import validate_email, EmailNotValidError
from sqlalchemy.exc import IntegrityError
from flask_jwt_extended import (
    create_access_token, set_access_cookies, 
    jwt_required, get_jwt_identity, unset_jwt_cookies
)
from extensions import db, mail, limiter
from models import User, EmailVerification

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

@auth_bp.route('/send-verification', methods=['POST'])
@limiter.limit("3 per minute")
def send_verification():
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get('email'))
    if not email:
        return jsonify({"status": "error", "message": "올바른 이메일을 입력해주세요."}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"status": "error", "message": "이미 가입된 이메일입니다."}), 400

    code = f"{secrets.randbelow(900000) + 100000:06d}"
    expires_at = datetime.now() + timedelta(minutes=3)

    # 재발송 시 이전 코드가 다시 사용되지 않도록 기존 기록을 정리합니다.
    EmailVerification.query.filter_by(email=email).delete(synchronize_session=False)
    verification = EmailVerification(email=email, code=code, expires_at=expires_at)
    db.session.add(verification)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Email verification record creation failed")
        return jsonify({"status": "error", "message": "인증번호 생성에 실패했습니다. 잠시 후 다시 시도해주세요."}), 500

    msg = Message("스터디 서버 회원가입 인증번호", sender=current_app.config['MAIL_USERNAME'], recipients=[email])
    msg.body = f"안녕하세요!\n회원가입 인증번호는 [{code}] 입니다.\n3분 이내에 입력해주세요."
    try:
        mail.send(msg)
        return jsonify({"status": "success", "message": "인증번호가 발송되었습니다."}), 200
    except Exception:
        db.session.rollback()
        try:
            EmailVerification.query.filter_by(id=verification.id).delete(synchronize_session=False)
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to remove verification record after mail error")
        current_app.logger.exception("Verification mail delivery failed")
        return jsonify({"status": "error", "message": "메일 발송에 실패했습니다. 이메일을 확인해주세요."}), 500

@auth_bp.route('/verify-code', methods=['POST'])
@limiter.limit("5 per minute")
def verify_code():
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get('email'))
    code = data.get('code')

    if not email or not isinstance(code, str) or not re.fullmatch(r'\d{6}', code):
        return jsonify({"status": "error", "message": "이메일과 6자리 인증번호를 확인해주세요."}), 400

    record = EmailVerification.query.filter_by(email=email).order_by(EmailVerification.id.desc()).first()
    if not record or record.code != code:
        return jsonify({"status": "error", "message": "인증번호가 일치하지 않습니다."}), 400
    if datetime.now() > record.expires_at:
        db.session.delete(record)
        db.session.commit()
        return jsonify({"status": "error", "message": "인증번호가 만료되었습니다."}), 400
        
    record.is_verified = True
    db.session.commit()
    return jsonify({"status": "success", "message": "이메일 인증이 완료되었습니다."}), 200

@auth_bp.route('/signup', methods=['POST'])
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

    verification = EmailVerification.query.filter_by(email=email, is_verified=True).order_by(EmailVerification.id.desc()).first()
    if not verification or datetime.now() > verification.expires_at:
        if verification:
            db.session.delete(verification)
            db.session.commit()
        return jsonify({"status": "error", "message": "이메일 인증을 먼저 완료해주세요."}), 403

    hashed_password = generate_password_hash(password)
    new_user = User(login_id=login_id, password=hashed_password, email=email, phone=phone)
    try:
        db.session.add(new_user)
        # 가입이 성공한 이메일의 인증 기록은 모두 제거하여 재사용을 차단합니다.
        EmailVerification.query.filter_by(email=email).delete(synchronize_session=False)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"status": "error", "message": "이미 사용 중인 아이디 또는 이메일입니다."}), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Signup transaction failed")
        return jsonify({"status": "error", "message": "회원가입 처리에 실패했습니다. 잠시 후 다시 시도해주세요."}), 500

    return jsonify({"status": "success", "message": "회원가입이 완료되었습니다!"}), 201

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
def logout():
    resp = jsonify({"status": "success", "message": "안전하게 로그아웃 되었습니다."})
    unset_jwt_cookies(resp)
    return resp, 200
