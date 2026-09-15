# Разработка в Docker

## Зачем это нужно

Все участники запускают одинаковые версии Python, Node.js, PostgreSQL и Redis одной командой. Локальная установка Python, Node.js и базы данных не нужна.

~~~mermaid
flowchart LR
  Browser[Браузер]
  Frontend[frontend: Vite / React]
  Backend[backend: FastAPI]
  Postgres[(PostgreSQL)]
  Redis[(Redis)]

  Browser -->|localhost:5173| Frontend
  Frontend -->|/api/* proxy| Backend
  Browser -->|localhost:8000/docs| Backend
  Backend --> Postgres
  Backend --> Redis
~~~

Frontend проксирует все запросы с пути **/api** в контейнер backend. Поэтому код frontend всегда вызывает относительный URL, например **/api/v1/health**, без localhost и без CORS-настроек.

## Первый запуск

1. Установить и запустить Docker Desktop.
2. В терминале в корне репозитория выполнить:

~~~powershell
docker compose up --build
~~~

3. Открыть http://localhost:5173. API-документация доступна на http://localhost:8000/docs.

При следующих запусках достаточно:

~~~powershell
docker compose up
~~~

Остановить контейнеры:

~~~powershell
docker compose down
~~~

Не используйте команду **docker compose down -v** без согласования с командой: она удаляет локальный том PostgreSQL и данные разработки.

## Как вносятся изменения

- Изменения в **backend/** видны контейнеру сразу; Uvicorn перезапускает API.
- Изменения в **frontend/** видны Vite сразу в браузере.
- **frontend_node_modules** и **postgres_data** — Docker volumes. Они не попадают в Git.
- Реальные токены держите только в локальном файле **.env** или в секретах среды развёртывания. В Git остаётся только **.env.example**.

## Что запускается

| Сервис | Адрес на компьютере | Назначение |
| --- | --- | --- |
| frontend | localhost:5173 | интерфейс React/Vite |
| backend | localhost:8000 | FastAPI и OpenAPI |
| postgres | только внутри Docker-сети | постоянные данные приложения |
| redis | только внутри Docker-сети | кэш и фоновые задачи |

Пароли PostgreSQL в compose.yaml предназначены только для локальной разработки. Перед развёртыванием значения передаются через секреты окружения.
