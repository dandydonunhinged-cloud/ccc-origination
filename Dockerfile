FROM python:3.12-slim

WORKDIR /app

# System deps for psycopg2-binary and any wheel builds
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

# Render injects $PORT; default 8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn app.app:app --host 0.0.0.0 --port ${PORT:-8080}"]