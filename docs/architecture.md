# Архитектура Repo Health

Документ описывает реализованную систему: код в `main`, миграции в `backend/migrations`,
запуск через `compose.yaml` и `compose.production.yaml`. Исходное предложение от 15 сентября,
написанное до реализации, сохранено в [architecture-proposal.md](architecture-proposal.md);
при расхождениях верен этот документ.

## Компоненты

~~~text
Браузер
  │  HTTPS, один origin
  ▼
Traefik (TLS) ─┬─► frontend: nginx, статическая сборка React (всё, кроме /api)
               │
               └─► /api, /health
                     ▼
                  backend: FastAPI, модульный монолит (uvicorn)
                     ├─ HTTP API: рейтинг, отчёты, запуск анализа, методика, вход, публичный API
                     ├─ in-process worker: выполняет анализы в фоне
                     ├─ планировщик: регулярно ставит публичные репозитории в очередь
                     ├─ анализаторы шести категорий + движок Score + сборщик отчёта
                     └─ интеграции ───► SourceCraft REST API (api.sourcecraft.tech)
                                   ├──► SourceCraft Git (временная рабочая копия)
                                   ├──► AppSec SourceCraft (snapshot из CLI или API сканов)
                                   ├──► Яндекс ID (OAuth 2.0 + PKCE)
                                   └──► Yandex AI Studio: YandexGPT Lite, необязательный AI-план
                     │
                     ▼
                  PostgreSQL: задания, снимки отчётов, расписание, сессии, подключения SourceCraft
~~~

| Компонент | Где в коде | Что делает |
| --- | --- | --- |
| Frontend | `frontend/` | React 18, TypeScript, Vite, Gravity UI. Не считает метрики: показывает готовый отчёт backend. Подробности — [frontend/README.md](../frontend/README.md) |
| HTTP API | `backend/app/main.py` | FastAPI. Контракт — [api-contract.md](api-contract.md), публичная часть — [public-api.md](public-api.md) |
| Вход | `backend/app/identity/yandex.py` | Authorization Code + PKCE с Яндекс ID. Сессия — httpOnly cookie; в PostgreSQL хранится только её хеш (`app_sessions`) |
| Подключение SourceCraft | `backend/app/identity/sourcecraft_connection.py` | Личный токен (PAT) пользователя для закрытых и внутренних репозиториев. Хранится зашифрованным (Fernet, ключ `SOURCECRAFT_CONNECTION_ENCRYPTION_KEY`) и в браузер не возвращается |
| Запуск и worker | `backend/app/analysis/` | Проверка доступа, задание в `analysis_jobs`, выполнение анализаторов в отдельном потоке, неизменяемый снимок отчёта. Жизненный цикл — [analysis-jobs.md](analysis-jobs.md) |
| Анализаторы | `backend/app/analyzers/` | По одному модулю на категорию; каждый возвращает общий `CategoryResult` (`backend/app/contracts.py`) |
| Score | `backend/app/scoring/` | Взвешенное среднее измеренных категорий, покрытие, ограничение за критическую уязвимость — [scoring-methodology.md](scoring-methodology.md) |
| Отчёт | `backend/app/reporting/` | JSON для интерфейса, Markdown для выгрузки, SVG-бейдж |
| AI-план | `backend/app/ai/` | Необязательно: YandexGPT Lite дописывает к готовым рекомендациям пошаговый план. Без `YANDEX_AI_STUDIO_API_KEY` выключен; при сбое модели анализ не падает — [recommendations.md](recommendations.md#ai-план-действий) |
| Рейтинг | `backend/app/leaderboard/` | Публичные проекции последних снимков, места только по Score — [leaderboard-policy.md](leaderboard-policy.md) |
| Планировщик | `backend/app/scheduling/` | Регулярный пересчёт публичного каталога — [scheduling-policy.md](scheduling-policy.md) |
| Интеграции | `backend/app/integrations/` | Клиенты SourceCraft API, Git, AppSec — [data-sources.md](data-sources.md) |

## Два пути анализа

**Свой репозиторий** (`POST /api/v1/repositories/{id}/analyses`):

1. Пользователь входит через Яндекс ID; в кабинете видит репозитории из `GET /api/v1/me/repositories`: с личным подключением — всё, что видит его токен в SourceCraft, без подключения — публичный каталог сервиса.
2. Backend заново проверяет доступ: личным токеном, если он подключён, иначе — только публичность репозитория. Сервисный токен не открывает закрытые и внутренние репозитории.
3. Resolver фиксирует полный SHA коммита ветки по умолчанию. Все анализаторы смотрят на одно состояние кода.
4. Worker выполняет анализаторы в отдельном потоке и собирает Score и рекомендации. Если AI включён, к рекомендациям дописывается AI-план. Снимок и terminal-статус сохраняются одной транзакцией.
5. Интерфейс опрашивает `GET /api/v1/analyses/{id}` каждые 3 секунды и открывает отчёт. Статус и подробный отчёт видит только тот, кто запускал анализ.

**Публичный рейтинг** (планировщик, флаг `PUBLIC_ANALYSIS_SCHEDULER_ENABLED`):

1. Раз в минуту планировщик сверяет публичный каталог SourceCraft (`SOURCECRAFT_DISCOVER_PUBLIC_REPOSITORIES=true` или `SOURCECRAFT_PUBLIC_ORGANIZATIONS`) с таблицей `analysis_schedules`.
2. Новые репозитории ставятся сразу, остальные — по активности: активные раз в 6 часов, обычные раз в сутки, давно не обновлявшиеся раз в 3 дня.
3. Запуск выполняет тот же worker от системного субъекта `system:public-scheduler`.
4. `GET /api/v1/leaderboard` строит рейтинг из последних снимков текущей методики, заново сверив каждый репозиторий с публичным каталогом.

## Хранение

| Таблица | Что хранит |
| --- | --- |
| `analysis_jobs` | задание: репозиторий, владелец (subject сессии), статус, время, worker, код ошибки |
| `analysis_snapshots` | неизменяемый отчёт готового анализа: JSON и Markdown |
| `analysis_worker_leases`, `analysis_job_recovery_state` | heartbeat worker и восстановление после остановки процесса |
| `analysis_schedules` | расписание публичного пересчёта: следующий запуск, reservation, счётчик ошибок, блокировка |
| `app_users`, `app_sessions`, `yandex_login_attempts` | пользователи Яндекс ID, хеши сессий, одноразовые state/PKCE входа |
| `sourcecraft_connections` | зашифрованный личный токен SourceCraft и логин |

Схема создаётся миграциями Alembic ([database-migrations.md](database-migrations.md)); backend применяет их перед стартом. Исходный код анализируемого репозитория в базу не попадает: он живёт только во временной рабочей копии на время анализа. Redis поднимается в Compose, но текущий код его не использует: очередь заданий и координация экземпляров построены на PostgreSQL.

## Масштабирование

- **Рост числа репозиториев.** Планировщик за проход ставит не больше 10 заданий и выбирает строки через `FOR UPDATE SKIP LOCKED`. Несколько экземпляров backend не возьмут один запуск дважды; подключение большого каталога не создаёт всплеск запросов к SourceCraft.
- **Частота пересчёта по активности.** Активный проект пересчитывается раз в 6 часов, спящий — раз в 3 дня. Добавка до 10% к интервалу разводит запуски во времени. Обоснование — [scheduling-policy.md](scheduling-policy.md).
- **Временная недоступность источников.** Повтор с удвоением задержки: 15 минут, 30 минут, 1 час… до 6 часов. Постоянная ошибка доступа блокирует запись, а не расходует токен бесконечно. Ошибка одной категории не отменяет анализ: она получает статус `error`, остальные считаются, итог помечается предварительным.
- **Крупные репозитории.** Границы по умолчанию покрывают крупный репозиторий из п. 9.2 ТЗ (от 10 000 файлов, 20 000 коммитов или 500 МБ):
  - до 50 000 файлов и 750 МБ рабочей копии, до 50 000 коммитов истории;
  - тайм-ауты Git и истории — 120 секунд;
  - Code health читает до 50 000 файлов и 100 МБ исходников.

  Все пределы задаются переменными `SOURCECRAFT_MAX_GIT_*`, `SOURCECRAFT_COMMIT_HISTORY_*`, `SOURCECRAFT_CODE_HEALTH_*`. Превышение предела не роняет анализ. Слишком большая рабочая копия переводит в статус `error` только Documentation и Code health, обрыв истории коммитов исключает одну метрику Activity. Остальные категории считаются, а итог помечается предварительным.
- **Горизонтальное масштабирование.** API не хранит состояние в памяти: сессии, задания и расписание лежат в PostgreSQL. Worker каждого экземпляра держит lease с heartbeat; незавершённые задания упавшего экземпляра завершаются кодом `worker_interrupted`, чужие активные не трогаются. Следующий шаг роста — вынести worker в отдельные процессы с общей очередью, не меняя HTTP-контракт ([analysis-jobs.md](analysis-jobs.md)).

## Безопасность

- Закрытые и внутренние репозитории анализируются только по личному подключению пользователя и только если у его токена есть доступ. Статус и подробный отчёт — только владельцу анализа: чужой и несуществующий ID дают одинаковый `404`.
- Публичные выдачи (рейтинг, бейдж, публичный API) строятся только по репозиториям, публичность которых SourceCraft подтвердил в момент запроса. Бейдж и публичный API отдают только Score, покрытие и статусы категорий — без рекомендаций, фактов и данных владельца анализа.
- Небезопасные запросы с cookie проверяются по `Origin` ([csrf-protection.md](csrf-protection.md)). Заголовки безопасности и `no-store` для личных ответов — [http-security.md](http-security.md). Production nginx добавляет CSP `script-src 'self'` и HSTS.
- Токены не пишутся в логи, отчёты и задания. Рабочая копия удаляется после анализа, Git запускается без запросов пароля и без «ленивых» догрузок объектов.
- CI проверяет SAST, зависимости, секреты, образы и SBOM ([ci-security.md](ci-security.md), [sbom.md](sbom.md)).

## Развёртывание

- **Разработка** — `docker compose up --build`: Vite с hot reload, backend с `--reload`, PostgreSQL и Redis ([docker-development.md](docker-development.md)).
- **Стенд** — `compose.production.yaml`: nginx со статической сборкой, backend без reload, миграции при старте, TLS на Traefik (`deploy/traefik/`). Выпуск, откат и smoke-проверка — [production-deployment-smoke.md](production-deployment-smoke.md).
