FROM python:3.13-slim

# Set environment variables for Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Create non-root user
RUN useradd -m appuser
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

# Ensure the persistent directory exists and is writable
RUN mkdir -p /railway/data && chown -R appuser:appuser /app /railway/data

USER appuser

CMD ["python", "triage.py"]
