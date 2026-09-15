# SourceCraft Repo Health

Веб-сервис оценивает здоровье репозиториев SourceCraft по шести категориям, объясняет итоговый Repo Health Score и предлагает действия по улучшению.

## Что уже согласовано

- Backend реализуется как модульный монолит на Python.
- Каждая категория возвращает общий `CategoryResult`: оценку, статус доступности данных, факты и рекомендации.
- Итоговый Score и Markdown-отчёт собираются общим ядром.
- Исходный код анализируемых репозиториев хранится только во временной рабочей области на время анализа.

Подробности:

- [Архитектура](docs/architecture-proposal.md)
- [Распределение ролей backend-команды](docs/backend-team-roles.md)

## Структура

```text
backend/
  app/
    contracts.py       # Общие модели между анализаторами и ядром
    main.py            # HTTP-приложение
    analyzers/         # Шесть независимых анализаторов
    integrations/      # SourceCraft API, CLI и Git
    scoring/           # Итоговый Repo Health Score
    reports/           # Веб- и Markdown-отчёты
  tests/
docs/
.github/
```

## Локальный старт

Проект требует Python 3.11 или новее.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
uvicorn backend.app.main:app --reload
```

После запуска проверка доступна по адресу `http://127.0.0.1:8000/health`.

Быстрая проверка общих контрактов без установки зависимостей:

```powershell
python -m unittest discover -s backend/tests
```

## Работа в команде

1. Перед началом работы обновите `main`.
2. Создайте ветку на одну задачу: `feature/readme-check` или `fix/cicd-pagination`.
3. Откройте pull request с кратким описанием входных данных, результата и проверки.
4. Получите проверку от другого участника команды.
5. Объединяйте небольшие готовые изменения регулярно, чтобы `main` оставался рабочим.

Правила подробнее описаны в [CONTRIBUTING.md](CONTRIBUTING.md).

## Ближайший общий результат

По одному репозиторию SourceCraft получить единый JSON и Markdown-отчёт с категориями, итоговой оценкой, причинами отсутствия данных и рекомендациями.
