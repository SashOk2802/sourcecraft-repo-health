FROM python:3.12-slim

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

COPY pyproject.toml README.md ./
COPY backend ./backend

RUN python -m pip install --upgrade pip \
    && python -m pip install -e ".[dev]" \
    && chown -R app:app /app

EXPOSE 8000

USER app

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
