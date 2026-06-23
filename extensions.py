from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from flask_mail import Mail
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db = SQLAlchemy()
jwt = JWTManager()
mail = Mail()
limiter = Limiter(key_func=get_remote_address, default_limits=["200 per day"], storage_uri="memory://")