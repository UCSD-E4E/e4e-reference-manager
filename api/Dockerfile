FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.14

# Resolve and install dependencies (not the project itself) for layer caching
COPY pyproject.toml .python-version ./
RUN uv sync --no-install-project

COPY app ./app

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
