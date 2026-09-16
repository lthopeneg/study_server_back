import re
from datetime import timedelta, timezone
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity, unset_jwt_cookies
from sqlalchemy import case
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db, limiter
from models import (
    DailyMainNews,
    PracticeProblemAttempt,
    PracticeProblemSet,
    SecurityNews,
    User,
    UserNewsBookmark,
)
from session_security import invalidate_user_sessions
from audit import record_audit_event

user_bp = Blueprint('user', __name__, url_prefix='/api/user')
KOREA_TIMEZONE = timezone(timedelta(hours=9), name='KST')

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


def build_learning_progress(user_id, language=None):
    published_query = PracticeProblemSet.query.filter_by(status='published')
    if language:
        published_query = published_query.filter_by(language=language)
    published = published_query.order_by(PracticeProblemSet.id.desc()).all()
    published_ids = {item.id for item in published}

    attempts_query = PracticeProblemAttempt.query.filter_by(user_id=user_id)
    if language:
        attempts_query = attempts_query.filter_by(language=language)
    total_attempts = attempts_query.count()
    recent = attempts_query.order_by(PracticeProblemAttempt.attempted_at.desc()).limit(10).all()

    per_problem = {}
    if published_ids:
        aggregate_query = db.session.query(
            PracticeProblemAttempt.problem_set_id,
            db.func.count(PracticeProblemAttempt.id),
            db.func.max(PracticeProblemAttempt.attempted_at),
            db.func.max(case((PracticeProblemAttempt.is_correct.is_(True), 1), else_=0)),
        ).filter(
            PracticeProblemAttempt.user_id == user_id,
            PracticeProblemAttempt.problem_set_id.in_(published_ids),
        )
        if language:
            aggregate_query = aggregate_query.filter(PracticeProblemAttempt.language == language)
        for problem_id, attempt_count, last_attempted_at, completed in aggregate_query.group_by(PracticeProblemAttempt.problem_set_id):
            per_problem[problem_id] = {
                'attempt_count': attempt_count,
                'completed': bool(completed),
                'last_attempted_at': to_korea_iso(last_attempted_at),
            }

    language_stats = {}
    for item in published:
        stats = language_stats.setdefault(item.language, {
            'total_problems': 0,
            'attempted_problems': 0,
            'completed_problems': 0,
            '_major_topics': {},
        })
        stats['total_problems'] += 1
        progress = per_problem.get(item.id)
        if progress:
            stats['attempted_problems'] += 1
            if progress['completed']:
                stats['completed_problems'] += 1
        major = stats['_major_topics'].setdefault(item.major_topic, {})
        topic = major.setdefault(item.minor_topic, {
            'name': item.minor_topic,
            'total_problems': 0,
            'completed_problems': 0,
        })
        topic['total_problems'] += 1
        if progress and progress['completed']:
            topic['completed_problems'] += 1

    total_topics = 0
    completed_topics = 0
    # Convert the internal topic maps to a stable, UI-friendly hierarchy.
    for stats in language_stats.values():
        raw_majors = stats.pop('_major_topics')
        major_topics = []
        language_total_topics = 0
        language_completed_topics = 0
        for major_name in sorted(raw_majors):
            topics = []
            for minor_name in sorted(raw_majors[major_name]):
                topic = raw_majors[major_name][minor_name]
                topic['completed'] = topic['completed_problems'] > 0
                topics.append(topic)
            major_completed = sum(1 for topic in topics if topic['completed'])
            language_total_topics += len(topics)
            language_completed_topics += major_completed
            major_topics.append({
                'name': major_name,
                'total_topics': len(topics),
                'completed_topics': major_completed,
                'topics': topics,
            })
        stats['total_topics'] = language_total_topics
        stats['completed_topics'] = language_completed_topics
        stats['completion_rate'] = round(language_completed_topics * 100 / language_total_topics) if language_total_topics else 0
        stats['major_topics'] = major_topics
        total_topics += language_total_topics
        completed_topics += language_completed_topics

    completed = sum(1 for item in per_problem.values() if item['completed'])
    return {
        'summary': {
            'total_problems': len(published),
            'attempted_problems': len(per_problem),
            'completed_problems': completed,
            'total_topics': total_topics,
            'completed_topics': completed_topics,
            'completion_rate': round(completed_topics * 100 / total_topics) if total_topics else 0,
            'total_attempts': total_attempts,
        },
        'by_language': language_stats,
        'per_problem': {str(problem_id): value for problem_id, value in per_problem.items()},
        'recent_attempts': [{
            'id': item.id,
            'problem_id': item.problem_set_id,
            'problem_title': item.problem_title,
            'language': item.language,
            'major_topic': item.major_topic,
            'minor_topic': item.minor_topic,
            'difficulty': item.difficulty,
            'correct': item.is_correct,
            'line_selection_correct': item.line_selection_correct,
            'secure_blank_correct': item.secure_blank_correct,
            'attempted_at': to_korea_iso(item.attempted_at),
            'problem_available': item.problem_set_id in published_ids,
        } for item in recent],
    }


def to_korea_iso(value):
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(KOREA_TIMEZONE).isoformat()


@user_bp.route('/learning-progress', methods=['GET'])
@jwt_required()
def get_learning_progress():
    language = request.args.get('language')
    if language not in {None, 'Python', 'C#'}:
        return jsonify({'status': 'error', 'message': '지원하지 않는 언어입니다.'}), 400
    user = User.query.filter_by(login_id=get_jwt_identity()).first()
    if not user:
        return jsonify({'status': 'error', 'message': '사용자를 찾을 수 없습니다.'}), 404
    return jsonify({'status': 'success', 'data': build_learning_progress(user.id, language)})


def serialize_news_bookmark(bookmark, ai_article_id=None):
    return {
        'id': bookmark.id,
        'item_type': bookmark.item_type,
        'news_id': bookmark.news_id,
        'title': bookmark.title,
        'url': bookmark.url,
        'source': bookmark.source,
        'published_at': bookmark.published_at,
        'created_at': to_korea_iso(bookmark.created_at),
        'ai_article_id': ai_article_id,
    }


@user_bp.route('/news-bookmarks', methods=['GET'])
@jwt_required()
def get_news_bookmarks():
    user = User.query.filter_by(login_id=get_jwt_identity()).first()
    if not user:
        return jsonify({'status': 'error', 'message': '사용자를 찾을 수 없습니다.'}), 404
    bookmarks = (
        UserNewsBookmark.query.filter_by(user_id=user.id)
        .order_by(UserNewsBookmark.created_at.desc(), UserNewsBookmark.id.desc())
        .all()
    )
    bookmark_urls = {item.url for item in bookmarks if item.url}
    ai_article_ids_by_url = {}
    if bookmark_urls:
        ai_articles = (
            DailyMainNews.query
            .filter(DailyMainNews.original_url.in_(bookmark_urls))
            .order_by(DailyMainNews.id.desc())
            .all()
        )
        for article in ai_articles:
            ai_article_ids_by_url.setdefault(article.original_url, article.id)
    return jsonify({
        'status': 'success',
        'data': [
            serialize_news_bookmark(item, ai_article_ids_by_url.get(item.url))
            for item in bookmarks
        ],
    })


@user_bp.route('/news-bookmarks', methods=['POST'])
@jwt_required()
def create_news_bookmark():
    user = User.query.filter_by(login_id=get_jwt_identity()).first()
    if not user:
        return jsonify({'status': 'error', 'message': '사용자를 찾을 수 없습니다.'}), 404
    data = request.get_json(silent=True) or {}
    item_type = data.get('item_type')
    news_id = data.get('news_id')
    if item_type not in {'security_news', 'daily_main'} or isinstance(news_id, bool) or not isinstance(news_id, int):
        return jsonify({'status': 'error', 'message': '스크랩할 뉴스 정보가 올바르지 않습니다.'}), 400

    existing = UserNewsBookmark.query.filter_by(
        user_id=user.id, item_type=item_type, news_id=news_id,
    ).first()
    if existing:
        return jsonify({'status': 'success', 'data': serialize_news_bookmark(existing)}), 200

    if item_type == 'security_news':
        news = db.session.get(SecurityNews, news_id)
        if not news:
            return jsonify({'status': 'error', 'message': '스크랩할 뉴스를 찾을 수 없습니다.'}), 404
        title, url, source = news.title, news.link, news.source
        published_at = news.pub_date
    else:
        news = db.session.get(DailyMainNews, news_id)
        if not news:
            return jsonify({'status': 'error', 'message': '스크랩할 AI 뉴스를 찾을 수 없습니다.'}), 404
        title, url, source = news.title, news.original_url, 'AI 메인 뉴스'
        published_at = news.created_at.strftime('%Y-%m-%d') if news.created_at else None

    if not isinstance(url, str) or not url.startswith(('https://', 'http://')):
        return jsonify({'status': 'error', 'message': '뉴스 원문 주소가 올바르지 않습니다.'}), 422

    bookmark = UserNewsBookmark(
        user_id=user.id,
        item_type=item_type,
        news_id=news_id,
        title=title,
        url=url,
        source=source,
        published_at=published_at,
    )
    db.session.add(bookmark)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        bookmark = UserNewsBookmark.query.filter_by(
            user_id=user.id, item_type=item_type, news_id=news_id,
        ).first()
        if not bookmark:
            raise
        return jsonify({'status': 'success', 'data': serialize_news_bookmark(bookmark)}), 200
    return jsonify({'status': 'success', 'data': serialize_news_bookmark(bookmark)}), 201


@user_bp.route('/news-bookmarks/<int:bookmark_id>', methods=['DELETE'])
@jwt_required()
def delete_news_bookmark(bookmark_id):
    user = User.query.filter_by(login_id=get_jwt_identity()).first()
    if not user:
        return jsonify({'status': 'error', 'message': '사용자를 찾을 수 없습니다.'}), 404
    bookmark = UserNewsBookmark.query.filter_by(id=bookmark_id, user_id=user.id).first()
    if not bookmark:
        return jsonify({'status': 'error', 'message': '스크랩한 뉴스를 찾을 수 없습니다.'}), 404
    db.session.delete(bookmark)
    db.session.commit()
    return jsonify({'status': 'success', 'message': '뉴스 스크랩을 해제했습니다.'})

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
    invalidate_user_sessions(user)
    db.session.commit()

    record_audit_event('account.password_change', actor=user, target_type='user', target_id=user.id)

    response = jsonify({"status": "success", "message": "비밀번호가 변경되었습니다. 새 비밀번호로 다시 로그인해 주세요."})
    unset_jwt_cookies(response)
    return response, 200

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
