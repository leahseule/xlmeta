# xlmeta 웹 데모 — 프로덕션 이미지 (gunicorn)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    XLMETA_DATA_DIR=/data/summaries

WORKDIR /app

# 의존성 먼저 복사 → 코드만 바뀌면 이 레이어는 캐시 재사용
COPY requirements.txt ./requirements.txt
COPY webapp/requirements.txt ./webapp/requirements.txt
RUN pip install --no-cache-dir -r webapp/requirements.txt

# 앱 코드
COPY . .

# 요약 저장 볼륨 + 비루트 사용자
RUN mkdir -p /data/summaries \
 && useradd --create-home appuser \
 && chown -R appuser /app /data
USER appuser

EXPOSE 8000
VOLUME ["/data"]

# 링크가 깔끔하도록(포트 없는 주소) compose에서 80:8000으로 매핑한다.
CMD ["sh", "-c", "gunicorn webapp.app:app --chdir /app --workers 2 --timeout 120 --bind 0.0.0.0:${PORT}"]
