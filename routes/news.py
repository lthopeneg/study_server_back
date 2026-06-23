import email.utils
from datetime import datetime
from flask import Blueprint, jsonify, request
from models import SecurityNews, DailyMainNews

# '/api/news' 로 시작하는 주소 묶음 선언
news_bp = Blueprint('news', __name__, url_prefix='/api/news')

@news_bp.route('/', methods=['GET'])
def get_news():
    try:
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 10))
        all_news = SecurityNews.query.all()
        
        def parse_to_datetime(news):
            if not news.pub_date:
                return news.created_at or datetime.min
            try:
                dt = email.utils.parsedate_to_datetime(news.pub_date)
                return dt.replace(tzinfo=None)
            except:
                try:
                    dt = datetime.fromisoformat(news.pub_date.replace(" ", "T"))
                    return dt.replace(tzinfo=None)
                except:
                    return news.created_at or datetime.min

        all_news.sort(key=parse_to_datetime, reverse=True)
        total_count = len(all_news)
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_news = all_news[start_idx:end_idx]
        
        result = []
        for news in paginated_news:
            dt = parse_to_datetime(news)
            display_date = "" if dt == datetime.min else dt.strftime("%Y-%m-%d %H:%M")
            result.append({
                "id": news.id, "title": news.title, "link": news.link, 
                "pub_date": display_date, "source": news.source
            })
            
        return jsonify({"status": "success", "data": result, "total": total_count, "page": page, "total_pages": (total_count + limit - 1) // limit}), 200
    except Exception as e:
        print("News API Error:", e)
        return jsonify({"status": "error", "message": "뉴스를 불러오는데 실패했습니다."}), 500

@news_bp.route('/ai-history', methods=['GET'])
def get_ai_news_history():
    try:
        news_list = DailyMainNews.query.order_by(DailyMainNews.created_at.desc()).all()
        result = [{
            "id": n.id, "title": n.title, "original_url": n.original_url, "created_at": n.created_at.strftime("%Y-%m-%d")
        } for n in news_list]
        return jsonify({"status": "success", "data": result}), 200
    except Exception as e:
        print("AI History API Error:", e)
        return jsonify({"status": "error", "message": "AI 뉴스 기록을 불러오는데 실패했습니다."}), 500

@news_bp.route('/daily-main', methods=['GET'])
def get_daily_main_news():
    try:
        news_id = request.args.get('id', type=int)
        if news_id:
            news = DailyMainNews.query.get(news_id)
        else:
            news = DailyMainNews.query.order_by(DailyMainNews.id.desc()).first()
            
        if not news:
            return jsonify({"status": "success", "data": None}), 200
            
        return jsonify({"status": "success", "data": {
            "id": news.id, "title": news.title, "content_md": news.content_md, 
            "original_url": news.original_url, "selection_reason": news.selection_reason, 
            "created_at": news.created_at.strftime("%Y-%m-%d %H:%M:%S")
        }}), 200
    except Exception as e:
        print("Daily Main News API Error:", e)
        return jsonify({"status": "error", "message": "AI 메인 뉴스를 불러오는데 실패했습니다."}), 500
