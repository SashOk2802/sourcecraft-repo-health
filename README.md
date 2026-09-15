# SourceCraft Repo Health

Веб-сервис оценивает здоровье репозиториев SourceCraft по шести категориям, объясняет итоговый Repo Health Score и предлагает действия по улучшению.

## Быстрый старт команды

Для единого окружения нужен Docker Desktop. Из корня репозитория:

~~~powershell
docker compose up --build
~~~

После старта:

- Frontend: http://localhost:5173
- Backend OpenAPI: http://localhost:8000/docs
- Проверка backend: http://localhost:8000/health

Остановка окружения:

~~~powershell
docker compose down
~~~

Docker не удаляет данные PostgreSQL при обычной остановке. Подробности: [разработка в Docker](docs/docker-development.md).

## Что уже согласовано

- Backend реализуется как модульный монолит на Python.
- Каждая категория возвращает общий CategoryResult: оценку, статус доступности данных, факты и рекомендации.
- Итоговый Score и Markdown-отчёт собираются общим ядром.
- Frontend получает готовый отчёт через HTTP API и не рассчитывает метрики.
- Исходный код анализируемых репозиториев хранится только во временной рабочей области на время анализа.

## Структура

~~~text
backend/             Python API и ядро анализа
frontend/            React/Vite интерфейс
docs/                архитектура, контракты, правила команды
compose.yaml         единый локальный запуск
~~~

## Документация

- [Архитектура](docs/architecture-proposal.md)
- [Распределение ролей backend-команды](docs/backend-team-roles.md)
- [Docker: запуск и правила](docs/docker-development.md)
- [Frontend: граница ответственности](docs/frontend-workspace.md)
- [Контракт API frontend ↔ backend](docs/api-contract.md)

## Работа в команде

1. Перед началом работы обновите main.
2. Создайте ветку на одну задачу: feature/readme-check или fix/cicd-pagination.
3. Откройте pull request с кратким описанием входных данных, результата и проверки.
4. Получите проверку от другого участника команды.
5. Объединяйте небольшие готовые изменения регулярно, чтобы main оставался рабочим.

Правила подробнее описаны в [CONTRIBUTING.md](CONTRIBUTING.md).
