from extensions import db

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True, autoincrement=True)
    login_id = db.Column(db.String(50), unique=True, nullable=False) 
    password = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    role = db.Column(db.String(20), nullable=True, default='USER')
    session_version = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

class PendingSignup(db.Model):
    __tablename__ = 'pending_signups'
    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True, autoincrement=True)
    login_id = db.Column(db.String(50), nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=False, index=True)
    phone = db.Column(db.String(20), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='pending', index=True)
    requested_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    decided_at = db.Column(db.DateTime, nullable=True)
    decided_by = db.Column(db.BigInteger, db.ForeignKey('users.id'), nullable=True)
    notification_status = db.Column(db.String(20), nullable=False, default='not_sent')
    notification_sent_at = db.Column(db.DateTime, nullable=True)

class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True, autoincrement=True)
    actor_user_id = db.Column(db.BigInteger, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    actor_login_id = db.Column(db.String(50), nullable=True)
    event_type = db.Column(db.String(80), nullable=False, index=True)
    target_type = db.Column(db.String(50), nullable=True)
    target_id = db.Column(db.String(100), nullable=True)
    outcome = db.Column(db.String(20), nullable=False, index=True)
    request_id = db.Column(db.String(32), nullable=True, index=True)
    ip_hash = db.Column(db.String(24), nullable=True)
    details_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now(), index=True)


class SecurityAlert(db.Model):
    __tablename__ = 'security_alerts'
    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True, autoincrement=True)
    fingerprint = db.Column(db.String(160), nullable=False, index=True)
    category = db.Column(db.String(50), nullable=False, index=True)
    severity = db.Column(db.String(20), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.String(500), nullable=False)
    actor_login_id = db.Column(db.String(50), nullable=True)
    ip_hash = db.Column(db.String(24), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='open', index=True)
    occurrence_count = db.Column(db.Integer, nullable=False, default=1)
    first_seen_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    last_seen_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now(), index=True)
    notified_at = db.Column(db.DateTime, nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    resolved_by = db.Column(db.BigInteger, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)


class UserSession(db.Model):
    __tablename__ = 'user_sessions'
    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True, autoincrement=True)
    session_id = db.Column(db.String(36), unique=True, nullable=False, index=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    ip_hash = db.Column(db.String(24), nullable=True)
    user_agent_hash = db.Column(db.String(24), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    last_seen_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    revoked_at = db.Column(db.DateTime, nullable=True, index=True)

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


class UserNewsBookmark(db.Model):
    __tablename__ = 'user_news_bookmarks'
    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True, autoincrement=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    item_type = db.Column(db.String(30), nullable=False)
    news_id = db.Column(db.BigInteger, nullable=False)
    title = db.Column(db.String(500), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    source = db.Column(db.String(100), nullable=True)
    published_at = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    __table_args__ = (
        db.UniqueConstraint('user_id', 'item_type', 'news_id', name='uq_user_news_bookmark'),
        db.Index('ix_user_news_bookmark_time', 'user_id', 'created_at'),
    )

class PracticeProblemSet(db.Model):
    __tablename__ = 'practice_problem_sets'
    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True, autoincrement=True)
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
    source_key = db.Column(db.String(255), unique=True, nullable=True)
    source_revision = db.Column(db.String(64), nullable=True)
    managed_by = db.Column(db.String(20), nullable=False, default='web')
    created_by = db.Column(db.BigInteger, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())
    variants = db.relationship('PracticeProblemVariant', backref='problem_set', cascade='all, delete-orphan')


class PracticeProblemAttempt(db.Model):
    __tablename__ = 'practice_problem_attempts'
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    problem_set_id = db.Column(
        db.BigInteger,
        db.ForeignKey('practice_problem_sets.id', ondelete='SET NULL'),
        nullable=True,
    )
    problem_title = db.Column(db.String(255), nullable=False)
    language = db.Column(db.String(20), nullable=False)
    major_topic = db.Column(db.String(100), nullable=False)
    minor_topic = db.Column(db.String(255), nullable=False)
    difficulty = db.Column(db.String(20), nullable=False)
    is_correct = db.Column(db.Boolean, nullable=False)
    line_selection_correct = db.Column(db.Boolean, nullable=False)
    secure_blank_correct = db.Column(db.Boolean, nullable=False)
    attempted_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    __table_args__ = (
        db.Index('ix_practice_attempt_user_problem', 'user_id', 'problem_set_id'),
        db.Index('ix_practice_attempt_user_time', 'user_id', 'attempted_at'),
    )

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


class PracticeProblemSyncState(db.Model):
    __tablename__ = 'practice_problem_sync_states'
    source_key = db.Column(db.String(255), primary_key=True)
    source_revision = db.Column(db.String(64), nullable=False)
    last_problem_id = db.Column(db.BigInteger, nullable=True)
    synced_at = db.Column(
        db.DateTime,
        nullable=False,
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )
