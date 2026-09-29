# SourceCraft Repo Health

Веб-сервис оценивает здоровье репозиториев SourceCraft по шкале от 0 до 100 — Repo Health Score. Он объясняет, из чего сложилась оценка, и даёт конкретные рекомендации по приоритету. Есть публичный рейтинг открытых репозиториев, анализ собственного репозитория после входа через Яндекс ID и выгрузка отчёта в Markdown и PDF.

**Стенд:** https://alhamdulylia.ru

Данные собираются только рабочими интерфейсами SourceCraft: REST API, Git и AppSec; готовых выгрузок нет. Score, приоритеты и факты детерминированные. Необязательный YandexGPT только дописывает к рекомендациям пошаговый AI-план ([data-sources.md](docs/data-sources.md#внешние-сервисы-и-ai)).

## Что реализовано

| Требование ТЗ | Как сделано | Подробнее |
| --- | --- | --- |
| Оценка по шести категориям (п. 3.1) | Security по результатам AppSec SourceCraft, CI/CD, Documentation, Activity, Issues, Code health; веса 25 / 20 / 20 / 15 / 15 / 5 | [методика](docs/scoring-methodology.md) |
| Repo Health Score (п. 3.2) | Взвешенное среднее измеренных категорий. «Нет данных» не равно нулю: такая категория не участвует в расчёте, а оценка помечается предварительной. Подтверждённая критическая уязвимость ограничивает Score значением 60 | [формула](docs/scoring-methodology.md) |
| Рекомендации (п. 3.3) | Приоритеты P0–P3; у каждой — проблема, почему это важно, факты со ссылками на SourceCraft, действие и ожидаемый эффект | [алгоритм](docs/recommendations.md) |
| Страница анализа (п. 3.4) | Название и ссылка, Score, фигура категорий, сильные и слабые стороны, объяснение расчёта, рекомендации, отсутствующие данные, дата и коммит, «Скачать .md» и «Скачать PDF» | [frontend](frontend/README.md) |
| Рейтинг (п. 4) | Место только по Score, ссылка на репозиторий, лайки, язык, последняя активность; фильтр по языку и поиск; сортировки по Score, лайкам и активности. **Частично:** строка ведёт на страницу анализа, но подробный отчёт планового снимка backend пока отдаёт только владельцу запуска, и посторонний увидит «Отчёта нет». Полный отчёт по публичному репозиторию получают своим запуском из кабинета | [правила рейтинга](docs/leaderboard-policy.md), [ограничения](docs/limitations.md#доступ) |
| Свой репозиторий (п. 5) | Вход через Яндекс ID, список доступных репозиториев, запуск, ход анализа, отчёт, повторная проверка. Закрытые и внутренние — по личному подключению SourceCraft, в правах пользователя | [архитектура](docs/architecture.md) |
| Периодический пересчёт (п. 6) | Планировщик: новые репозитории сразу, активные раз в 6 часов, обычные раз в сутки, спящие раз в 3 дня; повтор после сбоев с растущей задержкой | [политика пересчёта](docs/scheduling-policy.md) |
| Крупные репозитории (п. 9.2) | Пределы по умолчанию покрывают 10 000 файлов, 20 000 коммитов и 500 МБ рабочей копии и настраиваются переменными окружения | [масштабирование](docs/architecture.md#масштабирование) |

**Функции со звёздочкой** ([star-features.md](docs/star-features.md)):
- **Публичный API и бейдж для README** — реализованы, руководство — [public-api.md](docs/public-api.md).
- **Защита публичного рейтинга** — частично: место только по Score, лайки не входят в оценку, активность ограничена потолком.
- **AI-рекомендации** — частично: YandexGPT Lite дописывает к каждой рекомендации пошаговый план по фактам анализа (ключ `YANDEX_AI_STUDIO_API_KEY`); AI-summary нет.

## Зачем это нужно

- **Разработчикам и maintainers** — одна понятная оценка вместо ручного сбора сигналов из Git, CI, задач и AppSec. Плюс список действий по приоритету, где у каждого действия есть подтверждающие факты.
- **Тимлидам и CTO** — сравнимая, воспроизводимая и объяснимая оценка проектов: видно, какая часть тянет вниз и что даст исправление.
- **Командам, выбирающим открытые компоненты**, — рейтинг, где место зависит от здоровья проекта, а не от популярности.
- **Платформе SourceCraft** — единый аналитический слой над каталогом с регулярным пересчётом. Бейджи в README ведут на SourceCraft, публичный API встраивается в сайты и боты, а владельцы проектов получают стимул подтягивать CI, документацию и безопасность.

## Запуск

Нужен Docker с Compose. Все команды — из корня репозитория.

### Демо без ключей

~~~powershell
Copy-Item .env.example .env
Add-Content .env "VITE_USE_MOCKS=true"
docker compose up --build
~~~

- http://localhost:5173 — интерфейс на демо-данных: рейтинг, отчёты, кабинет (`?mock-user=1`);
- http://localhost:8000/docs — OpenAPI backend.

### С данными SourceCraft

Заполните в `.env` (комментарии — в [.env.example](.env.example)):

| Переменная | Зачем |
| --- | --- |
| `SOURCECRAFT_TOKEN` | сервисный токен SourceCraft для публичного каталога, рейтинга и пересчёта |
| `SOURCECRAFT_DISCOVER_PUBLIC_REPOSITORIES=true` или `SOURCECRAFT_PUBLIC_ORGANIZATIONS=org-one,org-two` | какой публичный каталог анализировать: весь или список организаций, ровно один режим |
| `PUBLIC_ANALYSIS_SCHEDULER_ENABLED=true` | регулярный пересчёт публичных репозиториев |
| `YANDEX_CLIENT_ID`, `YANDEX_REDIRECT_URI` | вход через Яндекс ID; локально redirect URI — `http://localhost:5173/api/v1/auth/yandex/callback`. `YANDEX_CLIENT_SECRET` — если его выдал тип OAuth-клиента |
| `YANDEX_AI_STUDIO_API_KEY`, `YANDEX_AI_MODEL` | необязательно: AI-план действий к рекомендациям через Yandex AI Studio; без ключа AI выключен |
| `SOURCECRAFT_CONNECTION_ENCRYPTION_KEY` | ключ Fernet для личных подключений SourceCraft (закрытые и внутренние репозитории): `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

Уберите строку `VITE_USE_MOCKS=true`, если добавляли её, и запустите `docker compose up --build`. Миграции PostgreSQL применяются автоматически. Подробности и проверка в Docker — [docker-development.md](docs/docker-development.md).

### Стенд

`compose.production.yaml`: статическая сборка frontend в nginx, backend без reload, PostgreSQL, TLS на Traefik. Выпуск, откат и smoke-проверка — [production-deployment-smoke.md](docs/production-deployment-smoke.md).

## Как повторить демонстрацию

1. **Рейтинг.** Фильтр по языку и сортировки. Подробный отчёт по публичному репозиторию — своим запуском из кабинета (шаг 3): отчёты плановых снимков пока видит только их владелец ([limitations.md](docs/limitations.md#доступ)).
2. **«Как считаем».** Веса, формула, статусы «нет данных», пересчёт.
3. **Свой репозиторий.** «Войти» → «Мои репозитории» → при необходимости подключить SourceCraft личным токеном → «Проверить». Видны ход анализа и готовый отчёт.
4. **Отчёт.** Категории, отсутствующие данные, рекомендации со ссылками на факты, «Скачать .md» и «Скачать PDF».
5. **Повторный анализ.** «Проверить снова» — обновлённые статус, время и результат.
6. **Пересчёт по расписанию.** Флаг `PUBLIC_ANALYSIS_SCHEDULER_ENABLED` и таблица `analysis_schedules` ([scheduling-policy.md](docs/scheduling-policy.md)).

## Документация

Состав по п. 9.5 ТЗ:

| Что требует ТЗ | Документ |
| --- | --- |
| Описание архитектуры | [architecture.md](docs/architecture.md) |
| Сборка и запуск | раздел «Запуск» выше, [docker-development.md](docs/docker-development.md), [production-deployment-smoke.md](docs/production-deployment-smoke.md), [frontend/README.md](frontend/README.md) |
| Используемые API и источники данных | [data-sources.md](docs/data-sources.md), [api-contract.md](docs/api-contract.md), [public-api.md](docs/public-api.md) |
| Формула Repo Health Score | [scoring-methodology.md](docs/scoring-methodology.md), раздел 1 |
| Перечень и описание метрик | [scoring-methodology.md](docs/scoring-methodology.md), разделы 2–4; [security-analyzer.md](docs/security-analyzer.md); [cicd-analyzer.md](docs/cicd-analyzer.md) |
| Обработка отсутствующих данных | [scoring-methodology.md](docs/scoring-methodology.md), «Статусы данных»; [security-analyzer.md](docs/security-analyzer.md) |
| Алгоритм формирования рекомендаций | [recommendations.md](docs/recommendations.md) |
| Ограничения решения | [limitations.md](docs/limitations.md) |
| Подход к масштабированию | [architecture.md](docs/architecture.md#масштабирование), [scheduling-policy.md](docs/scheduling-policy.md) |
| Функции со звёздочкой | [star-features.md](docs/star-features.md) |
| Внешние сервисы и AI | [data-sources.md](docs/data-sources.md#внешние-сервисы-и-ai) |

Подробности реализации:

- **Анализ и хранение:** [analysis-jobs.md](docs/analysis-jobs.md), [postgres-analysis-store.md](docs/postgres-analysis-store.md), [database-migrations.md](docs/database-migrations.md), [sourcecraft-production-analysis.md](docs/sourcecraft-production-analysis.md), [sourcecraft-repository-catalog.md](docs/sourcecraft-repository-catalog.md).
- **Данные SourceCraft:** [sourcecraft-capabilities.md](docs/sourcecraft-capabilities.md), [sourcecraft-appsec-api.md](docs/sourcecraft-appsec-api.md), [appsec-snapshot-refresh.md](docs/appsec-snapshot-refresh.md).
- **Безопасность и CI:** [http-security.md](docs/http-security.md), [csrf-protection.md](docs/csrf-protection.md), [ci-security.md](docs/ci-security.md), [ci-testing-guide.md](docs/ci-testing-guide.md), [sourcecraft-ci.md](docs/sourcecraft-ci.md), [sbom.md](docs/sbom.md).
- **Рабочие заметки команды и история решений:** [architecture-proposal.md](docs/architecture-proposal.md) (план до реализации), [backend-team-roles.md](docs/backend-team-roles.md), [frontend-workspace.md](docs/frontend-workspace.md), [frontend-review-response.md](docs/frontend-review-response.md), [methodology-owner-approval.md](docs/methodology-owner-approval.md), [pr-description.md](docs/pr-description.md).

## Структура

~~~text
backend/                  FastAPI: API, анализаторы, Score, отчёты, планировщик, интеграции
backend/migrations/       схема PostgreSQL (Alembic)
frontend/                 React + TypeScript + Vite + Gravity UI
docs/                     документация
deploy/traefik/           TLS и маршрутизация стенда
compose.yaml              локальный запуск
compose.production.yaml   стенд
.github/workflows/        CI: тесты, SAST, аудит зависимостей, секреты, образы, SBOM
.sourcecraft/ci.yaml      те же переносимые проверки в SourceCraft CI
~~~

## Проверки

CI на каждый pull request:
- backend: тесты и Ruff;
- frontend: типы, тесты Vitest и сборка;
- безопасность: SAST, аудит зависимостей Python и npm, поиск секретов, сканирование Docker-образов, SBOM;
- проверка Docker Compose.

Как запускать локально — [ci-testing-guide.md](docs/ci-testing-guide.md).

## Работа в команде

Ветка на задачу, pull request с описанием и проверкой, ревью другого участника. Прямых пушей в `main` нет. Правила — [CONTRIBUTING.md](CONTRIBUTING.md).
