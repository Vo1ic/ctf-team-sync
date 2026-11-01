FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev
COPY alembic.ini ./
COPY migrations ./migrations
RUN useradd --create-home app && mkdir -p /data/artifacts && chown -R app:app /data
USER app
EXPOSE 8000
CMD ["sh", "-c", "uv run --no-sync alembic upgrade head && exec uv run --no-sync uvicorn team_sync.api:create_app --factory --host 0.0.0.0 --port 8000"]
