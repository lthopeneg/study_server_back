"""AI 뉴스 저장을 짧은 DB 세션과 제한된 재시도로 처리합니다."""

from sqlalchemy.exc import OperationalError


def reset_session(db):
    """실패한 트랜잭션을 정리하고 풀의 연결을 확실히 반환합니다."""
    try:
        db.session.rollback()
    finally:
        db.session.remove()


def save_daily_main_news(db, model, article_data, max_attempts=2, log=print):
    """연결 종료 시 새 세션으로 한 번 재시도하고 중복 저장을 피합니다."""
    original_url = article_data["original_url"]

    for attempt in range(1, max_attempts + 1):
        try:
            existing = model.query.filter_by(original_url=original_url).first()
            if existing:
                log("   [안내] 동일한 원문 URL의 AI 뉴스가 이미 저장되어 있습니다.")
                return existing, False

            article = model(**article_data)
            db.session.add(article)
            db.session.commit()
            return article, True
        except OperationalError:
            reset_session(db)
            if attempt >= max_attempts:
                raise
            log("::warning title=AI 뉴스 DB 저장 재시도::DB 연결을 새로 맺어 저장을 한 번 더 시도합니다.")
        except Exception:
            reset_session(db)
            raise

    raise RuntimeError("AI 뉴스 저장 재시도 횟수를 초과했습니다.")
