# 가볍고 안정적인 파이썬 3.11 슬림 버전 사용
FROM python:3.11-slim

WORKDIR /app

# 패키지 목록 복사 및 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 전체 복사
COPY . .

# Gunicorn 서버 포트 노출
EXPOSE 5000

# Oracle Cloud 무료 티어의 메모리를 고려해 단일 프로세스와 2개 스레드로 실행
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "2", "--timeout", "60", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
