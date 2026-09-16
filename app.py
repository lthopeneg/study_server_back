import os
from flask import Flask
from flask_cors import CORS
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix

# 방금 만든 확장 모듈과 라우터(블루프린트) 가져오기
from extensions import db, jwt, mail, limiter
from routes.auth import auth_bp
from routes.news import news_bp
from routes.user import user_bp
from routes.practice import practice_bp

# 테이블 생성을 위해 모델 임포트 (app_context보다 위에서 호출 필수)
import models 
from schema_migrations import apply_schema_migrations
from runtime_safety import database_engine_options, install_request_logging
from security_config import install_security_headers, parse_boolean_setting, parse_cors_origins
from rate_limit_config import install_rate_limit_error_handler

load_dotenv()
app = Flask(__name__)
install_request_logging(app)
install_security_headers(app)

# --- 1. 설정 (Config) ---
# JWT 서명 키는 로컬 .env 또는 운영 배포 환경에서 반드시 주입해야 합니다.
# 누락된 상태로 공개 기본키를 사용하는 대신 서버 시작을 즉시 중단합니다.
app.config["JWT_SECRET_KEY"] = os.environ["JWT_SECRET_KEY"]
app.config["JWT_TOKEN_LOCATION"] = ["cookies"]
is_production = os.getenv("APP_ENV", "development").strip().lower() == "production"
if is_production:
    # 운영 백엔드는 외부에 직접 노출되지 않고 Caddy 한 단계를 통해서만 접근합니다.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config["JWT_COOKIE_SECURE"] = parse_boolean_setting(
    os.getenv("JWT_COOKIE_SECURE"), is_production,
)
app.config["JWT_COOKIE_CSRF_PROTECT"] = parse_boolean_setting(
    os.getenv("JWT_COOKIE_CSRF_PROTECT"), True,
)
app.config["JWT_COOKIE_SAMESITE"] = "Lax"

cors_origins = parse_cors_origins(os.getenv("CORS_ORIGINS"))
CORS(
    app,
    resources={r"/api/*": {"origins": cors_origins}},
    supports_credentials=True,
)

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
if not DB_PORT:
    DB_PORT = "3306"
DB_NAME = os.getenv("DB_NAME")
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL", f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = database_engine_options(app.config['SQLALCHEMY_DATABASE_URI'])

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv("MAIL_USERNAME")
app.config['MAIL_PASSWORD'] = os.getenv("MAIL_PASSWORD")
app.config['SIGNUP_APPROVAL_EMAIL'] = os.getenv("SIGNUP_APPROVAL_EMAIL", app.config['MAIL_USERNAME'])
app.config['PUBLIC_FRONTEND_URL'] = os.getenv('PUBLIC_FRONTEND_URL', 'https://scspace.duckdns.org').rstrip('/')
app.config['RATELIMIT_STORAGE_URI'] = os.getenv(
    'RATELIMIT_STORAGE_URI',
    'redis://study-rate-limit:6379/0' if is_production else 'memory://',
)
app.config['RATELIMIT_HEADERS_ENABLED'] = True

# --- 2. 확장 모듈 초기화 연결 (init_app) ---
db.init_app(app)
jwt.init_app(app)
mail.init_app(app)
limiter.init_app(app)
install_rate_limit_error_handler(app)

# 서버 켜질 때 테이블 존재 여부 확인 및 생성
with app.app_context():
    db.create_all()
    apply_schema_migrations()

# --- 3. 라우터 (Blueprint) 등록 ---
app.register_blueprint(auth_bp)
app.register_blueprint(news_bp)
app.register_blueprint(user_bp)
app.register_blueprint(practice_bp)

# 방금 만든 연구 노트 API 라우터 등록
from routes.notes import notes_bp
app.register_blueprint(notes_bp)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
