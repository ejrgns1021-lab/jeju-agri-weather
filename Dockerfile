# Cloud Run 용 Streamlit 컨테이너
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Cloud Run 은 PORT 환경변수(기본 8080)로 들어오는 요청을 전달합니다.
ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "streamlit run app.py --server.port=${PORT} --server.address=0.0.0.0"]
