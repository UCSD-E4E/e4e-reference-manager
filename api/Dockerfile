FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_NO_DEV=1

WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.14

# Run unprivileged. The venv is BUILT as this user (rather than chowned afterwards,
# which would copy the whole venv into a second layer), so it stays writable for a
# `uv run` that re-syncs (any run without UV_NO_SYNC) and uv's cache lands in its home.
RUN useradd --system --uid 10001 --user-group --create-home --home-dir /home/app app \
    && chown app:app /app
USER app

# Install locked dependencies (not the project itself) for layer caching
COPY --chown=app:app pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app app ./app
# Install the project too, so `uv run` needs no network at container start
RUN uv sync --frozen --no-dev

EXPOSE 8000
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"]
