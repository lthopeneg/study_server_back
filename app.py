import os
import random
import re
import email.utils # RSS 날짜 문자열 분석용 패키지 추가
from datetime import datetime, timedelta
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Mail, Message
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address


# .env 파일 로드
load_dotenv()

app = Flask(__name__)
CORS(app)

# DB 연결 설정
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME")
app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Flask-Mail 우체국 설정
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv("MAIL_USERNAME")
app.config['MAIL_PASSWORD'] = os.getenv("MAIL_PASSWORD")
mail = Mail(app)

# 무차별 대입(디도스/브루트포스) 방어용 Limiter 설정
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per day"], storage_uri="memory://")

# 1. User 테이블 모델
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    login_id = db.Column(db.String(50), unique=True, nullable=False) 
    password = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    role = db.Column(db.String(20), nullable=True, default='USER')
    created_at = db.Column(db.DateTime, server_default=db.func.now())

# 2. 이메일 인증 기록 임시 테이블 (자동 생성됨)
class EmailVerification(db.Model):
    __tablename__ = 'email_verifications'
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    email = db.Column(db.String(255), nullable=False)
    code = db.Column(db.String(6), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    is_verified = db.Column(db.Boolean, default=False)

# 3. 보안뉴스 수집용 테이블 (서버 재시작 시 자동 생성됨)
class SecurityNews(db.Model):
    __tablename__ = 'security_news'
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    title = db.Column(db.String(500), nullable=False)
    link = db.Column(db.String(500), unique=True, nullable=False) # 고유값(중복수집 방어)
    pub_date = db.Column(db.String(100), nullable=True)
    source = db.Column(db.String(100), nullable=True) # 뉴스 출처 (보안뉴스, 데일리시큐 등)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

# 서버 켜질 때 필요한 테이블 자동 생성 (존재하면 무시)
with app.app_context():
    db.create_all()

# 비밀번호 정규식 검사 함수 (영문+숫자+특수문자 최소 8자리)
def is_valid_password(password):
    return re.match(r'^(?=.*[a-zA-Z])(?=.*\d)(?=.*[\W_]).{8,}$', password) is not None

# ================= API 라우터 시작 =================

# [API] 1. 인증번호 메일 발송 (분당 3회 제한)
@app.route('/api/send-verification', methods=['POST'])
@limiter.limit("3 per minute")
def send_verification():
    email = request.json.get('email')
    if not email:
        return jsonify({"status": "error", "message": "이메일을 입력해주세요."}), 400
    
    # 중복 가입 방어
    if User.query.filter_by(email=email).first():
        return jsonify({"status": "error", "message": "이미 가입된 이메일입니다."}), 400

    # 6자리 랜덤 번호 및 3분 만료시간 생성
    code = str(random.randint(100000, 999999))
    expires_at = datetime.now() + timedelta(minutes=3)
    
    verification = EmailVerification(email=email, code=code, expires_at=expires_at)
    db.session.add(verification)
    db.session.commit()
    
    # 구글 메일 발송
    msg = Message("스터디 서버 회원가입 인증번호", sender=app.config['MAIL_USERNAME'], recipients=[email])
    msg.body = f"안녕하세요!\n회원가입 인증번호는 [{code}] 입니다.\n3분 이내에 입력해주세요."
    try:
        mail.send(msg)
        return jsonify({"status": "success", "message": "인증번호가 발송되었습니다."}), 200
    except Exception as e:
        print("Mail error:", e)
        return jsonify({"status": "error", "message": "메일 발송에 실패했습니다. 이메일을 확인해주세요."}), 500

# [API] 2. 인증번호 입력 후 검증 (분당 5회 제한)
@app.route('/api/verify-code', methods=['POST'])
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

# [API] 3. 회원가입 최종 제출
@app.route('/api/signup', methods=['POST'])
def signup():
    data = request.json
    login_id = data.get('login_id')
    password = data.get('password')
    email = data.get('email')
    phone = data.get('phone')
    
    # 1차 보안: 아이디 중복 체크
    if User.query.filter_by(login_id=login_id).first():
        return jsonify({"status": "error", "message": "이미 사용 중인 아이디입니다."}), 400
        
    # 2차 보안: 비밀번호 정규식(안전성) 검사
    if not is_valid_password(password):
        return jsonify({"status": "error", "message": "비밀번호는 영문, 숫자, 특수문자를 포함해 8자리 이상이어야 합니다."}), 400
        
    # 3차 보안: 이메일 인증 여부 검사 (우회 가입 방어)
    verification = EmailVerification.query.filter_by(email=email, is_verified=True).order_by(EmailVerification.id.desc()).first()
    if not verification:
        return jsonify({"status": "error", "message": "이메일 인증을 먼저 완료해주세요."}), 403
        
    # 4차 핵심 보안: 비밀번호 단방향 암호화 (해싱)
    hashed_password = generate_password_hash(password)
    new_user = User(login_id=login_id, password=hashed_password, email=email, phone=phone)
    db.session.add(new_user)
    db.session.commit()
    
    return jsonify({"status": "success", "message": "회원가입이 완료되었습니다!"}), 201

# [API] 4. 기존 로그인 로직 (보안 업그레이드)
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    req_user_id = data.get('userId')
    req_password = data.get('password')
    
    user = User.query.filter_by(login_id=req_user_id).first()
    
    # 평문 비교가 아닌 check_password_hash로 해시(Hash)된 문자열을 해독하여 비교
    if user and check_password_hash(user.password, req_password):
        return jsonify({
            "status": "success", 
            "username": user.login_id, 
            "message": f"{user.login_id}님 환영합니다!"
        }), 200
        
    return jsonify({"status": "error", "message": "아이디 또는 비밀번호가 잘못되었습니다."}), 401

# [API] 5. 보안뉴스 리스트 반환 (날짜 정렬 및 페이지네이션 적용)
@app.route('/api/news', methods=['GET'])
def get_news():
    try:
        # 프론트엔드에서 요청한 페이지 번호 (기본값 1페이지, 한 페이지당 10개)
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 10))
        
        # 1. DB에서 일단 전체 뉴스를 가져옵니다
        all_news = SecurityNews.query.all()
        
        # 2. 어떤 형태의 날짜 문자열이 들어와도 에러 없이 완벽하게 변환하는 함수
        def parse_to_datetime(news):
            if not news.pub_date:
                return news.created_at or datetime.min
            try:
                # 1차 시도: 보안뉴스 표준 RSS 포맷 (Tue, 2 Jun 2026...)
                dt = email.utils.parsedate_to_datetime(news.pub_date)
                # 시간대(timezone)를 제거해서 둘을 똑같은 기준으로 맞춰줌 (비교 에러 방지용)
                return dt.replace(tzinfo=None)
            except:
                try:
                    # 2차 시도: 데일리시큐 포맷 (YYYY-MM-DD HH:MM:SS)
                    dt = datetime.fromisoformat(news.pub_date.replace(" ", "T"))
                    return dt.replace(tzinfo=None)
                except:
                    return news.created_at or datetime.min

        # 3. 파이썬 메모리 상에서 최신 날짜순으로 완벽하게 재정렬
        all_news.sort(key=parse_to_datetime, reverse=True)
        
        # 4. 페이지네이션 (슬라이싱) 적용
        total_count = len(all_news)
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_news = all_news[start_idx:end_idx]
        
        result = []
        for news in paginated_news:
            # 프론트엔드 화면이 깔끔하도록 서로 다른 날짜 모양을 "2026-06-02 16:26" 처럼 예쁘게 통일!
            dt = parse_to_datetime(news)
            if dt == datetime.min:
                display_date = ""
            else:
                display_date = dt.strftime("%Y-%m-%d %H:%M")
                
            result.append({
                "id": news.id,
                "title": news.title,
                "link": news.link,
                "pub_date": display_date,
                "source": news.source
            })
            
        return jsonify({
            "status": "success", 
            "data": result,
            "total": total_count,
            "page": page,
            "total_pages": (total_count + limit - 1) // limit
        }), 200
    except Exception as e:
        print("News API Error:", e)
        return jsonify({"status": "error", "message": "뉴스를 불러오는데 실패했습니다."}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
