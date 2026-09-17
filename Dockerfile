FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY prometheus ./prometheus
COPY web ./web
COPY config.yaml ./config.yaml

EXPOSE 10000
CMD ["sh", "-c", "uvicorn prometheus.api.app:app --host 0.0.0.0 --port ${PORT:-10000}"]
