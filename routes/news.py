import email.utils
from datetime import datetime
from flask import Blueprint, jsonify, request, current_app
from sqlalchemy import case, func
from extensions import db
from models import SecurityNews, DailyMainNews

# '/api/news' 로 시작하는 주소 묶음 선언
news_bp = Blueprint('news', __name__, url_prefix='/api/news')

ISO_DATE_PREFIX_PATTERN = r'^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}'
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 50

def parse_positive_int_arg(name, default, maximum=None):
    raw_value = request.args.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return None
    if value < 1 or (maximum is not None and value > maximum):
        return None
    return value

def parse_news_date(news):
    if not news.pub_date:
        return news.created_at or datetime.min
    try:
        return email.utils.parsedate_to_datetime(news.pub_date).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError):
        try:
            return datetime.fromisoformat(news.pub_date.replace(" ", "T")).replace(tzinfo=None)
        except (TypeError, ValueError, OverflowError):
            return news.created_at or datetime.min

@news_bp.route('/', methods=['GET'])
def get_news():
    page = parse_positive_int_arg('page', 1)
    limit = parse_positive_int_arg('limit', DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)
    if page is None or limit is None:
        return jsonify({
            "status": "error",
            "message": f"page는 1 이상의 정수이고 limit은 1~{MAX_PAGE_SIZE} 사이의 정수여야 합니다."
        }), 400

    try:
        # ISO 8601(시간대 포함 가능)과 기존 RFC 822 날짜를 MySQL DateTime으로 변환합니다.
        # DB가 정렬과 페이지 슬라이싱을 담당해 전체 뉴스가 앱 메모리에 올라오지 않습니다.
        parsed_pub_date = case(
            (
                SecurityNews.pub_date.op('REGEXP')(ISO_DATE_PREFIX_PATTERN),
                func.str_to_date(
                    func.replace(func.left(SecurityNews.pub_date, 19), 'T', ' '),
                    '%Y-%m-%d %H:%i:%s'
                )
            ),
            else_=func.str_to_date(SecurityNews.pub_date, '%a, %e %b %Y %H:%i:%s +0900')
        )

        total_count = SecurityNews.query.count()
        paginated_news = (
            SecurityNews.query
            .order_by(
                parsed_pub_date.desc(),
                SecurityNews.created_at.desc(),
                SecurityNews.id.desc()
            )
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )

        result = []
        for news in paginated_news:
            dt = parse_news_date(news)
            display_date = "" if dt == datetime.min else dt.strftime("%Y-%m-%d %H:%M")
            result.append({
                "id": news.id, "title": news.title, "link": news.link, 
                "pub_date": display_date, "source": news.source
            })

        return jsonify({"status": "success", "data": result, "total": total_count, "page": page, "total_pages": (total_count + limit - 1) // limit}), 200
    except Exception:
        current_app.logger.exception("News list query failed")
        return jsonify({"status": "error", "message": "뉴스를 불러오는데 실패했습니다."}), 500

@news_bp.route('/ai-history', methods=['GET'])
def get_ai_news_history():
    page = parse_positive_int_arg('page', 1)
    limit = parse_positive_int_arg('limit', 12, MAX_PAGE_SIZE)
    if page is None or limit is None:
        return jsonify({
            "status": "error",
            "message": f"page는 1 이상의 정수이고 limit은 1~{MAX_PAGE_SIZE} 사이의 정수여야 합니다."
        }), 400

    try:
        total_count = DailyMainNews.query.count()
        news_list = (
            DailyMainNews.query
            .order_by(DailyMainNews.created_at.desc(), DailyMainNews.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        result = [{
            "id": n.id, "title": n.title, "original_url": n.original_url, "created_at": n.created_at.strftime("%Y-%m-%d")
        } for n in news_list]
        return jsonify({
            "status": "success",
            "data": result,
            "total": total_count,
            "page": page,
            "total_pages": (total_count + limit - 1) // limit
        }), 200
    except Exception:
        current_app.logger.exception("AI news history query failed")
        return jsonify({"status": "error", "message": "AI 뉴스 기록을 불러오는데 실패했습니다."}), 500

@news_bp.route('/daily-main', methods=['GET'])
def get_daily_main_news():
    try:
        news_id = request.args.get('id', type=int)
        if news_id:
            news = db.session.get(DailyMainNews, news_id)
        else:
            news = DailyMainNews.query.order_by(DailyMainNews.id.desc()).first()
            
        if not news:
            return jsonify({"status": "success", "data": None}), 200
            
        return jsonify({"status": "success", "data": {
            "id": news.id, "title": news.title, "content_md": news.content_md, 
            "original_url": news.original_url, "selection_reason": news.selection_reason, 
            "created_at": news.created_at.strftime("%Y-%m-%d %H:%M:%S")
        }}), 200
    except Exception:
        current_app.logger.exception("Daily main news query failed")
        return jsonify({"status": "error", "message": "AI 메인 뉴스를 불러오는데 실패했습니다."}), 500
