# 가볍고 안정적인 파이썬 3.11 슬림 버전 사용
FROM python:3.11-slim

WORKDIR /app

# 패키지 목록 복사 및 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 전체 복사
COPY . .

# Flask 서버 포트 노출
EXPOSE 5000

# 서버 실행 (host 0.0.0.0 설정이 되어있어야 외부에서 접근 가능)
CMD ["python", "app.py"]
