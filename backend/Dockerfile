# Investigation Agent Client — backend
# Runs the existing FastAPI app (app.main:app) on the project's backend port.
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code only — .env, .venv, caches and local databases are
# excluded via backend/.dockerignore.
COPY app ./app

# RISK_PLATFORM_BASE_URL (and LLM configuration) are provided through
# environment configuration at runtime — never baked in.
ENV APP_NAME="Investigation Agent API"

EXPOSE 8001

# Same startup as backend/start.sh, bound to all interfaces for containers.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--log-level", "info"]
