FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y git \
    && rm -rf /var/lib/apt/lists/*

# Этот GID — явный контракт с exporter'ом AppSec snapshot: файл получает
# group-read для 10001, а backend читает его из bind mount только как :ro.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --home-dir /app --shell /usr/sbin/nologin app

# Compose применяет миграции до запуска API. Конфигурация Alembic живёт в
# корне репозитория, поэтому она должна попасть в runtime-образ вместе с
# пакетом backend; иначе `alembic upgrade head` завершится до старта FastAPI.
COPY pyproject.toml README.md alembic.ini ./
COPY backend ./backend

RUN python -m pip install --upgrade pip \
    && python -m pip install -e ".[dev]" \
    && chown -R app:app /app

EXPOSE 8000

USER app

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
