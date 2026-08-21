import random
import re
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Message
from flask_jwt_extended import (
    create_access_token, set_access_cookies, 
    jwt_required, get_jwt_identity, unset_jwt_cookies
)
from extensions import db, mail, limiter
from models import User, EmailVerification

# '/api' 로 시작하는 주소 묶음 선언
auth_bp = Blueprint('auth', __name__, url_prefix='/api')

def is_valid_password(password):
    return re.match(r'^(?=.*[a-zA-Z])(?=.*\d)(?=.*[\W_]).{8,}$', password) is not None

@auth_bp.route('/send-verification', methods=['POST'])
@limiter.limit("3 per minute")
def send_verification():
    email = request.json.get('email')
    if not email:
        return jsonify({"status": "error", "message": "이메일을 입력해주세요."}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"status": "error", "message": "이미 가입된 이메일입니다."}), 400

    code = str(random.randint(100000, 999999))
    expires_at = datetime.now() + timedelta(minutes=3)
    
    verification = EmailVerification(email=email, code=code, expires_at=expires_at)
    db.session.add(verification)
    db.session.commit()
    
    msg = Message("스터디 서버 회원가입 인증번호", sender=current_app.config['MAIL_USERNAME'], recipients=[email])
    msg.body = f"안녕하세요!\n회원가입 인증번호는 [{code}] 입니다.\n3분 이내에 입력해주세요."
    try:
        mail.send(msg)
        return jsonify({"status": "success", "message": "인증번호가 발송되었습니다."}), 200
    except Exception as e:
        print("Mail error:", e)
        return jsonify({"status": "error", "message": "메일 발송에 실패했습니다. 이메일을 확인해주세요."}), 500

@auth_bp.route('/verify-code', methods=['POST'])
@limiter.limit("5 per minute")
def verify_code():
    data = request.json
    email = data.get('email')
    code = data.get('code')
    
    record = EmailVerification.query.filter_by(email=email).order_by(EmailVerification.id.desc()).first()
    if not record or record.code != code:
        return jsonify({"status": "error", "message": "인증번호가 일치하지 않습니다."}), 400
    if datetime.now() > record.expires_at:
        return jsonify({"status": "error", "message": "인증번호가 만료되었습니다."}), 400
        
    record.is_verified = True
    db.session.commit()
    return jsonify({"status": "success", "message": "이메일 인증이 완료되었습니다."}), 200

@auth_bp.route('/signup', methods=['POST'])
def signup():
    data = request.json
    login_id = data.get('login_id')
    password = data.get('password')
    email = data.get('email')
    phone = data.get('phone')
    
    if User.query.filter_by(login_id=login_id).first():
        return jsonify({"status": "error", "message": "이미 사용 중인 아이디입니다."}), 400
    if not is_valid_password(password):
        return jsonify({"status": "error", "message": "비밀번호는 영문, 숫자, 특수문자를 포함해 8자리 이상이어야 합니다."}), 400
        
    verification = EmailVerification.query.filter_by(email=email, is_verified=True).order_by(EmailVerification.id.desc()).first()
    if not verification:
        return jsonify({"status": "error", "message": "이메일 인증을 먼저 완료해주세요."}), 403
        
    hashed_password = generate_password_hash(password)
    new_user = User(login_id=login_id, password=hashed_password, email=email, phone=phone)
    db.session.add(new_user)
    db.session.commit()
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
