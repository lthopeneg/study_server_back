import os
from flask import Flask
from flask_cors import CORS
from dotenv import load_dotenv

# 방금 만든 확장 모듈과 라우터(블루프린트) 가져오기
from extensions import db, jwt, mail, limiter
from routes.auth import auth_bp
from routes.news import news_bp
from routes.user import user_bp

# 테이블 생성을 위해 모델 임포트 (app_context보다 위에서 호출 필수)
import models 

load_dotenv()
app = Flask(__name__)
CORS(app, supports_credentials=True)

# --- 1. 설정 (Config) ---
# JWT 서명 키는 로컬 .env 또는 운영 배포 환경에서 반드시 주입해야 합니다.
# 누락된 상태로 공개 기본키를 사용하는 대신 서버 시작을 즉시 중단합니다.
app.config["JWT_SECRET_KEY"] = os.environ["JWT_SECRET_KEY"]
app.config["JWT_TOKEN_LOCATION"] = ["cookies"]
app.config["JWT_COOKIE_SECURE"] = False 
app.config["JWT_COOKIE_CSRF_PROTECT"] = False 

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
if not DB_PORT:
    DB_PORT = "3306"
DB_NAME = os.getenv("DB_NAME")
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL", f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv("MAIL_USERNAME")
app.config['MAIL_PASSWORD'] = os.getenv("MAIL_PASSWORD")

# --- 2. 확장 모듈 초기화 연결 (init_app) ---
db.init_app(app)
jwt.init_app(app)
mail.init_app(app)
limiter.init_app(app)

# 서버 켜질 때 테이블 존재 여부 확인 및 생성
with app.app_context():
    db.create_all()

# --- 3. 라우터 (Blueprint) 등록 ---
app.register_blueprint(auth_bp)
app.register_blueprint(news_bp)
app.register_blueprint(user_bp)

# 방금 만든 연구 노트 API 라우터 등록
from routes.notes import notes_bp
app.register_blueprint(notes_bp)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
