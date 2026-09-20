FROM python:3.13-slim

# Set environment variables for Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copy and install dependencies first (for optimal Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . /app

# Set default persistent volume path and environment defaults
ENV GMAIL_TOKEN_PATH=/railway/data/token.json \
    STATE_DB_PATH=/railway/data/state.db \
    TICKETS_LOG=/railway/data/tickets_log.csv

# Ensure the persistent directory exists
RUN mkdir -p /railway/data

CMD ["python", "triage.py"]
