from extensions import db

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    login_id = db.Column(db.String(50), unique=True, nullable=False) 
    password = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    role = db.Column(db.String(20), nullable=True, default='USER')
    created_at = db.Column(db.DateTime, server_default=db.func.now())

class EmailVerification(db.Model):
    __tablename__ = 'email_verifications'
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    email = db.Column(db.String(255), nullable=False)
    code = db.Column(db.String(6), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    is_verified = db.Column(db.Boolean, default=False)

class SecurityNews(db.Model):
    __tablename__ = 'security_news'
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    title = db.Column(db.String(500), nullable=False)
    link = db.Column(db.String(500), unique=True, nullable=False)
    pub_date = db.Column(db.String(100), nullable=True)
    source = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

class DailyMainNews(db.Model):
    __tablename__ = 'daily_main_news'
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    title = db.Column(db.String(255), nullable=False)
    content_md = db.Column(db.Text, nullable=False)
    original_url = db.Column(db.String(500), nullable=False)
    selection_reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
