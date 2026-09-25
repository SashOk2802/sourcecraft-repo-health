# Контракт API frontend ↔ backend

Этот файл — соглашение команды о форме данных. Python-классы в `backend/app/contracts.py` остаются внутренними моделями backend. Frontend получает JSON, описанный здесь.

## Уже доступно

### GET /health и GET /api/v1/health

Ответ:

~~~json
{ "status": "ok" }
~~~

Первый путь используют Docker healthcheck и инфраструктура. Второй — frontend через proxy.

## Методика Score

### GET /api/v1/methodology

Возвращает публичное описание текущей версии методики для страницы «Как считаем». Ответ
строится из тех же констант, что использует расчёт Score, поэтому веса и названия
категорий не дублируются на frontend.

~~~json
{
  "version": "v1",
  "scoreRange": { "minimum": 0, "maximum": 100 },
  "categories": [
    { "code": "security", "label": "Безопасность", "weight": 25 }
  ],
  "aggregation": {
    "code": "weighted_average_of_measured_categories",
    "formula": "sum(categoryScore * weight) / sum(weight)",
    "coverageFormula": "measuredWeight / applicableWeight"
  },
  "dataStatuses": [
    {
      "code": "unavailable",
      "summary": "Источник не предоставил данные; это не нулевая оценка."
    }
  ],
  "scoreLimits": [
    { "code": "security-open-critical", "maximumScore": 60 }
  ]
}
~~~

`Coverage` показывает полноту измеренного веса, но не уменьшает итоговый Score.
Если Coverage меньше 1, оценка помечается как предварительная.

## Вход через Яндекс ID

Яндекс ID подтверждает личность пользователя в нашем сервисе. Он **не** даёт доступ к репозиториям SourceCraft: этот доступ будет оформляться отдельным подключением и проверяться resolver перед запуском анализа.

### GET /api/v1/auth/yandex/start

Начинает Authorization Code flow с PKCE. При настроенных `YANDEX_CLIENT_ID` и `YANDEX_REDIRECT_URI` отвечает `307` и перенаправляет браузер на официальный URL Яндекс ID. Параметры `state` и `code_verifier` одноразовые; `state` хранится сервером только в виде SHA-256-хеша не более 10 минут.

### GET /api/v1/auth/yandex/callback

Принимает от Яндекс ID `code` и `state`, обменивает code на сервере, получает только `id` и `login`, затем отвечает `303` на `/me/repositories`. Браузер получает cookie `repo_health_session` с флагами `HttpOnly`, `SameSite=Lax`, `Path=/`; OAuth-токен не записывается в cookie, ответ API или базу данных.

| Статус | Причина |
| --- | --- |
| 401 | Пользователь отменил вход, state истёк или уже был использован |
| 502 | Яндекс ID не ответил либо вернул некорректный ответ |
| 503 | Вход не настроен в окружении |

### GET /api/v1/me

Возвращает минимальный профиль текущей сессии:

~~~json
{ "id": "user-…", "login": "alex" }
~~~

Без действующей сессии возвращает `401`. Если Яндекс ID не сконфигурирован, возвращает `503`.

### GET /api/v1/me/repositories

Возвращает список **публичных** репозиториев из организаций,
заданных в `SOURCECRAFT_PUBLIC_ORGANIZATIONS`. Нужна действующая сессия
Яндекс ID. В первой версии это общий безопасный каталог: вход не даёт доступа
к private/internal репозиториям SourceCraft.

~~~json
{
  "repositories": [
    {
      "id": "repo-42",
      "organizationSlug": "team",
      "repositorySlug": "platform-api",
      "name": "team/platform-api",
      "url": "https://sourcecraft.dev/team/platform-api",
      "defaultBranch": "main",
      "language": "Python",
      "isEmpty": false
    }
  ],
  "total": 1
}
~~~

`id` — непрозрачный идентификатор, который frontend передаёт в
`POST /api/v1/repositories/{repository_id}/analyses`. Его не нужно составлять
из `organizationSlug` и `repositorySlug`.

| Статус | Причина |
| --- | --- |
| 401 | Нет действующей сессии Яндекс ID |
| 503 | Не задана пара `SOURCECRAFT_TOKEN` и `SOURCECRAFT_PUBLIC_ORGANIZATIONS`, либо каталог SourceCraft недоступен |
### POST /api/v1/auth/logout

Отзывает серверную сессию и удаляет cookie. Возвращает `204` даже если cookie уже отсутствует.

### Конфигурация

Для включения входа нужны обе переменные: `YANDEX_CLIENT_ID` и `YANDEX_REDIRECT_URI`. Последний адрес должен в точности совпадать с redirect URI в настройках приложения Яндекс ID; для локального Docker это обычно `http://localhost:5173/api/v1/auth/yandex/callback`. `YANDEX_CLIENT_SECRET` добавляют, только если он выдан типу OAuth-клиента. В production `YANDEX_SESSION_COOKIE_SECURE=true`; для локального HTTP Docker compose устанавливает `false`. Значения с секретами хранят только в `.env` или секретах среды развёртывания.

## Запуск и состояние анализа

### POST /api/v1/repositories/{repository_id}/analyses

Создаёт задание в состоянии `queued` от имени аутентифицированного пользователя и передаёт его обработчику. Возвращает `202 Accepted`.

~~~json
{
  "id": "analysis-2026-09-21",
  "status": "queued",
  "repository": { "id": "repo-42" },
  "score": null,
  "isPreliminary": null,
  "createdAt": "2026-09-21T12:30:00Z",
  "startedAt": null,
  "finishedAt": null,
  "error": null,
  "reportUrl": null,
  "markdownReportUrl": null
}
~~~

Ошибки:

| Статус | Причина |
| --- | --- |
| 401 | Запрос не содержит проверенную пользовательскую идентичность |
| 403 | Запрошен private/internal репозиторий либо у пользователя нет доступа к репозиторию |
| 404 | Репозиторий не найден |
| 422 | Передан некорректный ID репозитория |
| 503 | Не настроен dispatcher, аутентификация либо недоступен каталог SourceCraft |

HTTP-слой передаёт в dispatcher только проверенный `AnalysisPrincipal` без токенов и секретов. При заданных `SOURCECRAFT_TOKEN` и `SOURCECRAFT_PUBLIC_ORGANIZATIONS` стандартное приложение создаёт production dispatcher: resolver ищет репозиторий в перечисленных организациях и разрешает только `visibility: public`. Даже если сервисный токен технически видит private/internal репозиторий, такой запрос завершается `403`; Яндекс ID сам по себе не расширяет права SourceCraft.

Без полной пары переменных dispatcher не создаётся и endpoint отвечает `503`. Это безопасный переходный режим до отдельного пользовательского подключения SourceCraft, которое потребуется для показа и анализа private/internal репозиториев. Конфигурация, ограничения и состав категорий описаны в [документе production-анализатора](sourcecraft-production-analysis.md). Для тестов и локальной интеграции `create_app` принимает готовые dispatcher и principal provider.

### GET /api/v1/analyses/{analysis_id}

Возвращает актуальное состояние одного анализа: `queued`, `running`, `completed`, `partial` или `failed`.

Пока запуск выполняется, `score`, `isPreliminary` и ссылки на отчёты равны `null`. После `completed` или `partial` backend подставляет Score, данные репозитория и ссылки на сохранённый отчёт. Для `failed` поле `error` содержит безопасный код и текст ошибки.

Пример завершённого частичного анализа:

~~~json
{
  "id": "analysis-2026-09-15",
  "status": "partial",
  "repository": { "id": "repo-42", "name": "team/platform-api" },
  "score": 73.4,
  "isPreliminary": true,
  "createdAt": "2026-09-15T12:00:00Z",
  "startedAt": "2026-09-15T12:00:01Z",
  "finishedAt": "2026-09-15T12:00:04Z",
  "error": null,
  "reportUrl": "/api/v1/analyses/analysis-2026-09-15/report",
  "markdownReportUrl": "/api/v1/analyses/analysis-2026-09-15/report.md"
}
~~~

## Отчёты

### GET /api/v1/analyses/{analysis_id}/report

Возвращает JSON-отчёт готового снимка анализа. Пока отчёта нет или идентификатор неизвестен, backend отвечает `404`.

### GET /api/v1/analyses/{analysis_id}/report.md

Возвращает Markdown для того же снимка и также отвечает `404`, пока результат не сохранён.

При заданной переменной DATABASE_URL снимки и задания хранятся в PostgreSQL и доступны после перезапуска backend. Без DATABASE_URL используется временное хранилище в памяти только для локальных тестов. Подробнее — в docs/postgres-analysis-store.md.

## Формат JSON-отчёта

JSON-модель строится в `backend/app/reporting/builder.py`; endpoint возвращает её без дополнительного преобразования.

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
    "status": "partial",
    "analyzedAt": "2026-09-15T12:30:00Z",
    "commitSha": "d7bebd1",
    "methodologyVersion": "v1",
    "coverage": 0.75,
    "isPreliminary": true,
    "scoreLimit": null
  },
  "score": 73.4,
  "scoreDetails": {
    "measuredWeight": 75,
    "applicableWeight": 100
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
      "evidence": []
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
      "evidence": [
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

Если применено ограничение из-за открытой критической AppSec-уязвимости, `score` содержит ограниченный результат, а в `analysis.scoreLimit` добавляется объект:

~~~json
{
  "value": 60,
  "uncappedScore": 90,
  "code": "security-open-critical",
  "summary": "Есть подтверждённая открытая критическая AppSec-уязвимость."
}
~~~

## Обязательные правила

| Поле | Правило |
| --- | --- |
| score | число от 0 до 100 или null; null не заменяют нулём |
| analysis_id | от 1 до 128 символов A–Z, a–z, 0–9, ., _, ~ или -; первый символ — буква или цифра |
| analysis.coverage | число от 0 до 1; null, если для репозитория нет применимых категорий |
| analysis.isPreliminary | true, когда доступна только часть применимых категорий |
| analysis.status | partial для предварительного результата, completed для полного или неприменимого набора категорий |
| analysis.scoreLimit | null либо объект с value, uncappedScore, code и summary |
| categories[].code | security, cicd, documentation, activity, issues или code_health |
| categories[].status | measured, unavailable, not_applicable, insufficient_sample или error |
| categories[].weight | исходный вес категории в методике v1 |
| categories[].effectiveWeight | вес категории среди измеренных; null, если категория не участвовала в Score |
| categories[].points | фактический вклад категории в Score; null, если категория не участвовала |
| recommendation.priority | p0, p1, p2 или p3 |
| recommendation.expectedScoreDelta | ожидаемое изменение итогового Score или null, если его нельзя оценить надёжно |
| reason | машинный код, объясняющий, почему score равен null |
| categories[].evidence и recommendations[].evidence | факты и ссылки, на которых основаны оценка и рекомендация |

## Как менять контракт

1. Сначала изменить этот файл и добавить пример ответа.
2. Backend добавляет поле с обратной совместимостью.
3. Frontend использует новое поле и показывает состояние null явно.
4. Удалять или переименовывать поле можно только после согласования с frontend.

Формат API использует camelCase. Внутренние Python-модели могут оставаться snake_case; преобразование выполняет слой HTTP API.
