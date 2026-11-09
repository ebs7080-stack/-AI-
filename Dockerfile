# Railway 등 컨테이너 호스팅용 이미지.
# 문법 오류 분류가 3.12 오류 메시지에 맞춰져 있으므로 파이썬 버전을 3.12 로 고정한다.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

# 공개 운영 기본값. DB 는 영구 볼륨(/data)에 둔다. CWA_SIGNUP_CODE 는 호스팅 서비스의 환경 변수로 정한다.
ENV CWA_PUBLIC=1 \
    CWA_DB_PATH=/data/data.db \
    PORT=8000

# 채팅 허브가 메모리에 있고 DB 가 SQLite 라서 worker 는 1개만 띄운다.
# 앞단 프록시가 HTTPS 를 처리하므로 X-Forwarded-* 헤더를 믿는다.
CMD mkdir -p "$(dirname "$CWA_DB_PATH")" && \
    exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --workers 1 \
    --proxy-headers --forwarded-allow-ips="*"
