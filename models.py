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

class PracticeProblemSet(db.Model):
    __tablename__ = 'practice_problem_sets'
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    title = db.Column(db.String(255), nullable=False)
    language = db.Column(db.String(20), nullable=False)
    runtime_platform = db.Column(db.String(30), nullable=True)
    project_type = db.Column(db.String(30), nullable=True)
    major_topic = db.Column(db.String(100), nullable=False)
    minor_topic = db.Column(db.String(255), nullable=False)
    difficulty = db.Column(db.String(20), nullable=False)
    scenario = db.Column(db.Text, nullable=True)
    creation_method = db.Column(db.String(20), nullable=False, default='manual')
    status = db.Column(db.String(20), nullable=False, default='draft')
    created_by = db.Column(db.BigInteger, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())
    variants = db.relationship('PracticeProblemVariant', backref='problem_set', cascade='all, delete-orphan')

class PracticeProblemVariant(db.Model):
    __tablename__ = 'practice_problem_variants'
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    problem_set_id = db.Column(db.BigInteger, db.ForeignKey('practice_problem_sets.id'), nullable=False)
    problem_type = db.Column(db.String(30), nullable=False)
    answers_json = db.Column(db.Text, nullable=False)
    files = db.relationship('PracticeProblemFile', backref='variant', cascade='all, delete-orphan')
    __table_args__ = (
        db.UniqueConstraint('problem_set_id', 'problem_type', name='uq_problem_set_type'),
    )

class PracticeProblemFile(db.Model):
    __tablename__ = 'practice_problem_files'
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    variant_id = db.Column(db.BigInteger, db.ForeignKey('practice_problem_variants.id'), nullable=False)
    filename = db.Column(db.String(120), nullable=False)
    content = db.Column(db.Text, nullable=False)
    hint = db.Column(db.Text, nullable=True)
    display_order = db.Column(db.Integer, nullable=False, default=0)
    __table_args__ = (
        db.UniqueConstraint('variant_id', 'filename', name='uq_problem_variant_filename'),
    )
