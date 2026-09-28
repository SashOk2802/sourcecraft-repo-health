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

Перед запуском FastAPI Docker применяет все миграции PostgreSQL. При следующих запусках достаточно:

~~~powershell
docker compose up
~~~

Текущую версию схемы можно посмотреть командой:

~~~powershell
docker compose exec backend alembic current
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
| backend-test | запускается только с profile `test` | полный набор backend-тестов и Ruff |
| postgres | только внутри Docker-сети | постоянные данные приложения |
| redis | только внутри Docker-сети | кэш и фоновые задачи |

Пароли PostgreSQL в compose.yaml предназначены только для локальной разработки. Перед развёртыванием значения передаются через секреты окружения.

## Полная проверка backend в Docker

Обычный `backend` — сервис разработки: он применяет миграции и запускает API.
Для полного тестового набора используйте отдельный сервис, который не стартует
при `docker compose up` и не подключает PostgreSQL или Redis:

~~~powershell
docker compose --profile test run --rm --no-deps backend-test
~~~

Он монтирует `frontend`, `scripts`, `.github` и `compose.yaml` только для чтения,
потому что часть статических тестов проверяет Dockerfile, workflow, Compose и
скрипт редактирования fixtures. Запуск не меняет `postgres_data` и
`frontend_node_modules`.

## Права внутри контейнеров

Backend и frontend-контейнеры создают пользователя `app`, передают ему файлы
приложения и запускаются с `USER app`. Поэтому даже при ошибке в приложении код
внутри контейнера не получает права `root` по умолчанию. Установка зависимостей
в образе выполняется до переключения пользователя; в рантайме повышенных прав
нет. Это защита контейнера, а не замена обновления зависимостей или проверки
входных данных приложения.

## Вход через Яндекс ID

По умолчанию вход выключен: без пары `YANDEX_CLIENT_ID` и `YANDEX_REDIRECT_URI` API отвечает `503`, а остальные сценарии разработки продолжают работать. Чтобы проверить вход локально:

1. Зарегистрируйте тестовый OAuth-клиент в Яндекс ID с redirect URI `http://localhost:5173/api/v1/auth/yandex/callback`.
2. Скопируйте `.env.example` в `.env` и заполните `YANDEX_CLIENT_ID`, `YANDEX_REDIRECT_URI`; `YANDEX_CLIENT_SECRET` нужен только если его выдал Яндекс ID.
3. Оставьте `YANDEX_SESSION_COOKIE_SECURE=false` только для локального HTTP и выполните `docker compose up --build`.

OAuth-токен не попадает в браузер, отчёты или логи. В развёрнутой среде redirect URI использует HTTPS, а `YANDEX_SESSION_COOKIE_SECURE` должен быть `true`.

## Анализ публичных репозиториев SourceCraft

Чтобы включить production-анализ, заполните в `.env` обе переменные:

~~~dotenv
SOURCECRAFT_TOKEN=...
SOURCECRAFT_DISCOVER_PUBLIC_REPOSITORIES=true
~~~

Флаг включает официальный глобальный public-каталог SourceCraft. Если нужен
ограниченный список, оставьте флаг `false` и задайте вместо него
`SOURCECRAFT_PUBLIC_ORGANIZATIONS=org-one,org-two`. Одновременно включать оба
режима нельзя. Анализ разрешён лишь для репозиториев с `visibility: public`;
private/internal запросы получают `403`. При неполной конфигурации dispatcher не
запускается и endpoint анализа вернёт `503`. Чистый `docker compose up` показывает
этот ответ backend, а не подменяет его mock-данными. Для офлайн-демо fixtures
добавьте в `.env` `VITE_USE_MOCKS=true` и перезапустите Compose. Подробности о
категориях и безопасном git-клоне — в
[документе production-анализатора](sourcecraft-production-analysis.md).
