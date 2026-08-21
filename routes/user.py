import re
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db, limiter
from models import User

user_bp = Blueprint('user', __name__, url_prefix='/api/user')

def is_valid_password(password):
    return re.match(r'^(?=.*[a-zA-Z])(?=.*\d)(?=.*[\W_]).{8,}$', password) is not None

# [API] 사용자 프로필 정보 조회
@user_bp.route('/profile', methods=['GET'])
@jwt_required()
def get_profile():
    current_user_id = get_jwt_identity()
    user = User.query.filter_by(login_id=current_user_id).first()
    
    if not user:
        return jsonify({"status": "error", "message": "사용자를 찾을 수 없습니다."}), 404
        
    return jsonify({
        "status": "success",
        "data": {
            "login_id": user.login_id,
            "email": user.email,
            "phone": user.phone,
            "role": user.role,
            "created_at": user.created_at.strftime("%Y-%m-%d") if user.created_at else ""
        }
    }), 200

# [API] 비밀번호 변경
@user_bp.route('/password', methods=['PUT'])
@jwt_required()
@limiter.limit("5 per hour")
def change_password():
    current_user_id = get_jwt_identity()
    user = User.query.filter_by(login_id=current_user_id).first()
    
    if not user:
        return jsonify({"status": "error", "message": "사용자를 찾을 수 없습니다."}), 404
        
    data = request.json
    current_password = data.get('current_password')
    new_password = data.get('new_password')
    
    if not current_password or not new_password:
        return jsonify({"status": "error", "message": "현재 비밀번호와 새 비밀번호를 모두 입력해주세요."}), 400
        
    # 1차 방어: 현재 비밀번호 일치 확인
    if not check_password_hash(user.password, current_password):
        return jsonify({"status": "error", "message": "현재 비밀번호가 일치하지 않습니다."}), 401
        
    # 2차 방어: 기존과 동일한 비밀번호 차단
    if current_password == new_password:
        return jsonify({"status": "error", "message": "새 비밀번호는 현재 비밀번호와 달라야 합니다."}), 400
        
    # 3차 방어: 새 비밀번호 정규식(안전성) 검사
    if not is_valid_password(new_password):
        return jsonify({"status": "error", "message": "비밀번호는 영문, 숫자, 특수문자를 포함해 8자리 이상이어야 합니다."}), 400
        
    # 4차: 통과 시 해싱하여 DB 저장
    user.password = generate_password_hash(new_password)
    db.session.commit()
    
    return jsonify({"status": "success", "message": "비밀번호가 성공적으로 변경되었습니다. 2초 뒤 자동 로그아웃됩니다."}), 200

# [API] 현재 비밀번호 일치 여부 단순 검증 (회원정보수정 진입용 2차 인증)
@user_bp.route('/verify-password', methods=['POST'])
@jwt_required()
@limiter.limit("5 per minute")
def verify_password():
    current_user_id = get_jwt_identity()
    user = User.query.filter_by(login_id=current_user_id).first()
    
    if not user:
        return jsonify({"status": "error", "message": "사용자를 찾을 수 없습니다."}), 404
        
    password = request.json.get('password')
    if not password:
        return jsonify({"status": "error", "message": "비밀번호를 입력해주세요."}), 400
        
    if check_password_hash(user.password, password):
        return jsonify({"status": "success", "message": "인증에 성공했습니다."}), 200
        
    return jsonify({"status": "error", "message": "비밀번호가 일치하지 않습니다."}), 401
