FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.18
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-python-downloads
COPY backend ./backend
COPY alembic.ini ./
COPY frontend/public/auth.css ./frontend/public/auth.css
COPY frontend/public/fonts ./frontend/public/fonts
COPY scripts/container-entry.py ./scripts/container-entry.py
RUN useradd --uid 10001 --create-home app && mkdir -p /app/data && chown app:app /app/data
USER app
ENTRYPOINT ["/app/.venv/bin/python", "scripts/container-entry.py"]
CMD ["-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
