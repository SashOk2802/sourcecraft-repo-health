# Контракт API frontend ↔ backend

Этот файл — соглашение команды о форме данных. Python-классы в backend/app/contracts.py остаются внутренними моделями backend. Frontend получает JSON, описанный здесь.

## Уже доступно

### GET /health и GET /api/v1/health

Ответ:

~~~json
{ "status": "ok" }
~~~

Первый путь используют Docker healthcheck и инфраструктура. Второй — frontend через proxy.

## Планируемый отчёт

Следующим общим endpoint будет:

~~~text
GET /api/v1/repositories/{organization_slug}/{repository_slug}/report
~~~

Он пока не реализован. Frontend использует mock, но форма будущего ответа фиксируется заранее:

~~~json
{
  "repository": {
    "id": "repo-42",
    "organizationSlug": "team",
    "repositorySlug": "platform-api",
    "name": "team/platform-api",
    "url": "https://sourcecraft.example/team/platform-api"
  },
  "analysis": {
    "id": "analysis-2026-09-15",
    "status": "completed",
    "analyzedAt": "2026-09-15T12:30:00Z",
    "methodologyVersion": "v1",
    "coverage": 0.83
  },
  "score": 76,
  "categories": [
    {
      "code": "security",
      "label": "Безопасность",
      "status": "measured",
      "score": 61,
      "summary": "Есть зависимости, требующие обновления.",
      "reason": null
    },
    {
      "code": "dependencies",
      "label": "Зависимости",
      "status": "unavailable",
      "score": null,
      "summary": "Данные не получены.",
      "reason": "Внешний реестр недоступен."
    }
  ],
  "recommendations": [
    {
      "code": "security-update-dependencies",
      "priority": "p0",
      "problem": "Есть зависимости с известными уязвимостями.",
      "action": "Обновить пакеты и повторно запустить анализ.",
      "rationale": "Обновление снижает риск.",
      "expectedEffect": "Повышение оценки безопасности.",
      "evidence": [
        {
          "source": "dependency-scanner",
          "reference": "package-x@1.2.0",
          "summary": "Найдена уязвимость.",
          "url": null
        }
      ]
    }
  ]
}
~~~

## Обязательные правила

| Поле | Правило |
| --- | --- |
| score | число от 0 до 100 или null; null не заменяют нулём |
| coverage | число от 0 до 1; null, если для репозитория нет применимых категорий |
| categories[].status | measured, unavailable, not_applicable, insufficient_sample или error |
| recommendation.priority | p0, p1, p2 или p3 |
| reason | объясняет, почему score равен null |
| evidence | факты и ссылки, на которых основана рекомендация |

## Как менять контракт

1. Сначала изменить этот файл и добавить пример ответа.
2. Backend добавляет поле с обратной совместимостью.
3. Frontend использует новое поле и показывает состояние null явно.
4. Удалять или переименовывать поле можно только после согласования с frontend.

Формат API использует camelCase. Внутренние Python-модели могут оставаться snake_case; преобразование выполняет слой HTTP API.
