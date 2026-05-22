import os
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

app = Flask(__name__)
CORS(app)

# DB 연결 설정 (PyMySQL 사용)
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME")

# SQLAlchemy 설정
app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# User 모델 정의 (이미 존재하는 users 테이블과 구조 매핑)
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    login_id = db.Column(db.String(50), unique=True, nullable=False) 
    password = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    role = db.Column(db.String(20), nullable=True, default='USER')
    created_at = db.Column(db.DateTime, server_default=db.func.now())
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    req_user_id = data.get('userId')
    req_password = data.get('password')
    
    if not req_user_id or not req_password:
        return jsonify({"status": "error", "message": "아이디와 비밀번호를 모두 입력해주세요."}), 400
    # 실제 DB에서 유저 아이디 검색 (user_id가 아닌 login_id 컬럼으로 검색)
    user = User.query.filter_by(login_id=req_user_id).first()
    
    # 유저가 존재하고, 비밀번호가 일치하는지 확인 (현재 평문 비교)
    if user and user.password == req_password:
        return jsonify({
            "status": "success", 
            "username": user.login_id, 
            "message": f"{user.login_id}님 환영합니다!"
        }), 200
        
    return jsonify({
        "status": "error", 
        "message": "아이디 또는 비밀번호가 잘못되었습니다."
    }), 401
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)