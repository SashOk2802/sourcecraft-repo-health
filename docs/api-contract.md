# Контракт API frontend ↔ backend

Этот файл — соглашение команды о форме данных. Python-классы в `backend/app/contracts.py` остаются внутренними моделями backend. Frontend получает JSON, описанный здесь.

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

Он пока не реализован. Модель отчёта уже строится в `backend/app/reporting/builder.py`: HTTP-слой будет получать из него готовый JSON.

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
    "commitSha": "d7bebd1",
    "methodologyVersion": "v1",
    "coverage": 0.75,
    "isPreliminary": true
  },
  "score": 73.4,
  "scoreDetails": {
    "uncappedScore": 73.4,
    "measuredWeight": 75,
    "applicableWeight": 100,
    "scoreLimit": null
  },
  "categories": [
    {
      "code": "security",
      "label": "Безопасность",
      "status": "unavailable",
      "score": null,
      "weight": 25,
      "effectiveWeight": null,
      "points": null,
      "summary": "Результаты AppSec не получены.",
      "reason": "appsec_not_available",
      "facts": []
    },
    {
      "code": "cicd",
      "label": "CI/CD",
      "status": "measured",
      "score": 58,
      "weight": 20,
      "effectiveWeight": 26.67,
      "points": 15.47,
      "summary": "9 из 40 последних прогонов завершились неуспешно.",
      "reason": null,
      "facts": [
        {
          "code": "failed-runs",
          "value": 9,
          "normalizedScore": 58,
          "summary": "Есть повторяющиеся падения e2e-тестов.",
          "evidence": []
        }
      ]
    }
  ],
  "recommendations": [
    {
      "code": "cicd-fix-e2e",
      "priority": "p0",
      "problem": "Падают e2e-тесты.",
      "action": "Разобраться с падениями e2e-тестов.",
      "rationale": "Повторяющиеся падения снижают оценку CI/CD.",
      "expectedEffect": "CI/CD поднимется примерно с 58 до 80.",
      "expectedScoreDelta": 5.9,
      "evidence": [
        {
          "source": "sourcecraft-cicd",
          "reference": "run-4812",
          "summary": "Таймаут ожидания браузера.",
          "url": null
        }
      ]
    }
  ]
}
~~~

Если применено ограничение из-за открытой критической AppSec-уязвимости, `score` содержит ограниченный результат, а в `scoreDetails` добавляется объект:

~~~json
{
  "uncappedScore": 90,
  "scoreLimit": {
    "maximumScore": 60,
    "code": "security-open-critical",
    "summary": "Есть подтверждённая открытая критическая AppSec-уязвимость."
  }
}
~~~

## Обязательные правила

| Поле | Правило |
| --- | --- |
| score | число от 0 до 100 или null; null не заменяют нулём |
| analysis.coverage | число от 0 до 1; null, если для репозитория нет применимых категорий |
| analysis.isPreliminary | true, когда доступна только часть применимых категорий |
| categories[].code | security, cicd, documentation, activity, issues или code_health |
| categories[].status | measured, unavailable, not_applicable, insufficient_sample или error |
| categories[].weight | исходный вес категории в методике v1 |
| categories[].effectiveWeight | вес категории среди измеренных; null, если категория не участвовала в Score |
| categories[].points | фактический вклад категории в Score; null, если категория не участвовала |
| recommendation.priority | p0, p1, p2 или p3 |
| recommendation.expectedScoreDelta | ожидаемое изменение итогового Score или null, если его нельзя оценить надёжно |
| reason | машинный код, объясняющий, почему score равен null |
| facts и evidence | факты и ссылки, на которых основаны оценка и рекомендация |

## Как менять контракт

1. Сначала изменить этот файл и добавить пример ответа.
2. Backend добавляет поле с обратной совместимостью.
3. Frontend использует новое поле и показывает состояние null явно.
4. Удалять или переименовывать поле можно только после согласования с frontend.

Формат API использует camelCase. Внутренние Python-модели могут оставаться snake_case; преобразование выполняет слой HTTP API.
