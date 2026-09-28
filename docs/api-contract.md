# Контракт API frontend ↔ backend

Этот файл — соглашение команды о форме данных. Python-классы в `backend/app/contracts.py` остаются внутренними моделями backend. Frontend получает JSON, описанный здесь.

## Уже доступно

### GET /health и GET /api/v1/health

Ответ:

~~~json
{ "status": "ok" }
~~~

Первый путь используют Docker healthcheck и инфраструктура. Второй — frontend через proxy.

## Публичный рейтинг

### GET /api/v1/leaderboard

Возвращает рейтинг только по подтверждённым public-репозиториям и только для текущей опубликованной версии методики. Аутентификация не требуется. В ответ не попадают private/internal репозитории и снимки, у которых ID, организация или slug не совпали с текущей записью public-каталога.

| Query-параметр | Значение по умолчанию | Правило |
| --- | --- | --- |
| `language` | отсутствует | точное совпадение языка без учёта регистра |
| `search` | отсутствует | подстрока в `organization/repository` |
| `sort` | `score` | `score`, `likes` или `activity`; меняет порядок строк, но не место |
| `includePreliminary` | `false` | при `true` добавляет отдельный список предварительных оценок без места |
| `page` | `1` | положительное число страницы полных оценок |
| `pageSize` | `15` | от 1 до 100; применяется только к `items` |

~~~json
{
  "items": [
    {
      "place": 1,
      "analysisId": "analysis-2026-09-25",
      "repository": {
        "id": "repo-42",
        "organizationSlug": "team",
        "repositorySlug": "platform-api",
        "name": "team/platform-api",
        "url": "https://sourcecraft.dev/team/platform-api",
        "description": null,
        "language": "Python"
      },
      "score": 90,
      "coverage": 1,
      "isPreliminary": false,
      "scoreLimited": false,
      "likes": null,
      "lastActivityAt": null,
      "analyzedAt": "2026-09-25T12:00:00Z",
      "categories": [
        { "code": "security", "label": "Безопасность", "status": "measured", "score": 90 }
      ]
    }
  ],
  "preliminary": [],
  "total": 1,
  "preliminaryTotal": 2,
  "page": 1,
  "pageSize": 15,
  "languages": [{ "name": "Python", "count": 1 }],
  "updatedAt": "2026-09-25T12:00:00Z",
  "pendingCount": 3,
  "methodologyVersion": "v2"
}
~~~

`place` получают только полные числовые Score выбранной методики. Равные Score получают спортивное место (`1, 2, 2, 4`), которое рассчитывается до фильтров и UI-сортировки. `preliminary` всегда содержит строки без места; туда попадают частичные оценки, а также готовые отчёты без числового Score. `total` считает только отфильтрованные полные строки, `preliminaryTotal` — все отфильтрованные предварительные, даже когда сам блок не запрошен.

`pendingCount` — public-репозитории, у которых нет пригодного снимка текущей методики. `updatedAt` — самый новый `analyzedAt` среди пригодных public-снимков текущей версии или `null`. `languages` строится по таким же проанализированным строкам после `search`, но до фильтра `language`, чтобы селектор языка не исчезал после выбора. `likes` — сумма публичных положительных реакций SourceCraft, `lastActivityAt` — его проверенное поле `last_updated`; если источник не отдал поле, API возвращает `null`, а соответствующая сортировка помещает такую строку после строк с данными.

Если public-каталог не сконфигурирован, endpoint отвечает `503`. При полной паре `SOURCECRAFT_TOKEN` и `SOURCECRAFT_PUBLIC_ORGANIZATIONS` production-приложение собирает рейтинг через тот же каталог, что и список «Мои репозитории».
## Методика Score

### GET /api/v1/methodology

Возвращает публичное описание текущей версии методики для страницы «Как считаем». Ответ
строится из тех же констант, что использует расчёт Score, поэтому веса и названия
категорий не дублируются на frontend.

~~~json
{
  "version": "v2",
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

### Личное подключение SourceCraft

Яндекс ID отвечает только за личность в нашем сервисе. Чтобы пользователь
добровольно дал доступ к своим private/internal репозиториям, frontend передаёт
SourceCraft PAT ровно один раз в `POST /api/v1/connections/sourcecraft`.
Backend проверяет PAT запросом `GET /user` к официальному API SourceCraft,
сохраняет только ciphertext с authenticated encryption и связывает его с
`user.id` текущей Яндекс-сессии. Сам PAT, ciphertext и технические детали
ответа SourceCraft не попадают в HTTP-ответы, логи или `repr` объектов.

`GET /api/v1/connections/sourcecraft` возвращает только безопасное состояние:

~~~json
{ "connected": true, "login": "artem", "connectedAt": "2026-09-28T12:00:00Z" }
~~~

Если подключения нет, поля `login` и `connectedAt` равны `null`. `DELETE
/api/v1/connections/sourcecraft` безвозвратно удаляет ciphertext и возвращает
`204`; повторный delete безопасен. Все три endpoint требуют действующую
Яндекс-сессию. POST и DELETE также проходят общую CSRF-проверку `Origin`.

| Статус | Причина |
| --- | --- |
| 401 | Нет сессии, либо SourceCraft отклонил переданный PAT |
| 404 | Vault отключён: не задан `SOURCECRAFT_CONNECTION_ENCRYPTION_KEY` |
| 422 | Токен имеет недопустимый формат |
| 503 | SourceCraft недоступен или его ответ нельзя безопасно проверить |

После подключения этот же server-side credential используется для личного
каталога и запуска анализа. Браузер больше не передаёт PAT: он выбирает только
непрозрачный `repository_id`, а backend сам связывает запрос с текущей
Яндекс-сессией.

### GET /api/v1/me/repositories

Нужна действующая сессия Яндекс ID. Если пользователь подключил SourceCraft,
endpoint читает `GET /me/repos` его личным PAT и возвращает доступные ему
`public`, `internal` и `private` репозитории. Если личного подключения нет,
возвращается только общий public-каталог из организаций, заданных в
`SOURCECRAFT_PUBLIC_ORGANIZATIONS`.

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
      "visibility": "internal",
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
| 401 | Нет действующей сессии Яндекс ID, либо личный PAT отозван и подключение нужно обновить |
| 503 | Личное подключение повреждено или SourceCraft недоступен; без личного подключения также не настроен либо недоступен public-каталог |
### POST /api/v1/auth/logout

Отзывает серверную сессию и удаляет cookie. Возвращает `204` даже если cookie уже отсутствует.
Когда в запросе есть сессионная cookie, браузер должен передать `Origin`, совпадающий с
origin из `YANDEX_REDIRECT_URI`; чужой origin получает `403` до выполнения logout.
Подробности — в [документе о CSRF-защите](csrf-protection.md).

### Конфигурация

Для включения входа нужны обе переменные: `YANDEX_CLIENT_ID` и `YANDEX_REDIRECT_URI`. Последний адрес должен в точности совпадать с redirect URI в настройках приложения Яндекс ID и иметь путь `/api/v1/auth/yandex/callback`; для локального Docker это обычно `http://localhost:5173/api/v1/auth/yandex/callback`. `YANDEX_CLIENT_SECRET` добавляют, только если он выдан типу OAuth-клиента. В production используйте HTTPS и `YANDEX_SESSION_COOKIE_SECURE=true`; для локального HTTP Docker compose устанавливает `false`. Значения с секретами хранят только в `.env` или секретах среды развёртывания.

Личный SourceCraft vault включается отдельно переменной
`SOURCECRAFT_CONNECTION_ENCRYPTION_KEY`: это один 32-байтный ключ Fernet в
urlsafe-base64, не PAT. Сгенерируйте его один раз в защищённой среде командой
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
и храните только в секретах окружения. Потеря или замена ключа делает ранее
зашифрованные подключения намеренно нечитаемыми; для пользователей нужно
предложить подключить SourceCraft заново. Ключ нельзя ротировать, просто
перезаписав переменную: это требует отдельной миграции ciphertext.

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
| 401 | Нет сессии Яндекс ID (`Authentication required.`), либо SourceCraft отклонил сохранённый личный PAT (`SourceCraft rejected the token.`) |
| 403 | Личный PAT принят, но к выбранному репозиторию нет доступа |
| 404 | Репозиторий не найден |
| 409 | Личного подключения нет, а репозиторий не входит в разрешённый public-каталог |
| 422 | Передан некорректный ID репозитория |
| 429 | SourceCraft ограничил частоту запросов. Заголовок `Retry-After` есть, только если источник прислал число секунд |
| 502 | Ответ SourceCraft нельзя превратить в контекст анализа |
| 503 | Не настроен dispatcher или аутентификация, повреждено личное подключение либо недоступен каталог или SourceCraft |

HTTP-слой передаёт в dispatcher только проверенный `AnalysisPrincipal`: это
`user.id` сессии Яндекс ID, без токена SourceCraft. Режим выбирается только на
сервере. При наличии личного подключения backend проверяет доступ к выбранному
репозиторию личным PAT. Activity, Issues и CI/CD используют его только внутри
коротких API-вызовов; Documentation и Code health получают ограниченный
временный Git-workspace; Security читает только безопасный AppSec snapshot.
PAT не записывается в context, job, отчёт или базу данных в открытом виде.

Без личного подключения разрешён только public fallback из пары
`SOURCECRAFT_TOKEN` и `SOURCECRAFT_PUBLIC_ORGANIZATIONS`. Сервисный токен не
открывает private/internal. Яндекс ID сам по себе права SourceCraft не расширяет.
Конфигурация описана в [документе production-анализатора](sourcecraft-production-analysis.md).

`create_sourcecraft_app()` — отдельный пользовательский запуск Activity и Issues, не вход процесса. Bearer этого запроса читает репозиторий и в subject не копируется. Отказ в доступе — `403`. Чужой id в теле ответа SourceCraft — `502`, не `404`. Чтение статуса и отчёта требует ту же сессию, что создала задание. Чужой subject и неизвестный id отвечают одним `404`. Снимок без строки задания с владельцем не отдаётся.

### GET /api/v1/analyses/{analysis_id}

Возвращает состояние одного анализа только владельцу: `queued`, `running`, `completed`, `partial` или `failed`. Без сессии — `401`. Чужой или неизвестный id — `404`.

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

Возвращает JSON-отчёт готового снимка только владельцу задания. Пока отчёта нет, id неизвестен или сессия чужая, backend отвечает `404`. Без сессии — `401`.

### GET /api/v1/analyses/{analysis_id}/report.md

Возвращает Markdown для того же снимка и также отвечает `404`, пока результат не сохранён.

### SVG-бейджи

`GET /api/v1/analyses/{analysis_id}/badge.svg` и
`GET /api/v1/repositories/{organization_slug}/{repository_slug}/badge.svg`
возвращают SVG с округлённым Score для встраивания в README. Они не требуют
сессии и разрешены только для репозиториев, которые прямо сейчас подтверждены
public-каталогом. Для private/internal, неизвестного или временно недоступного
каталога оба endpoint возвращают нейтральный бейдж `unknown` без данных
репозитория. Ответы имеют `Cache-Control: public, max-age=300, s-maxage=300`,
`Access-Control-Allow-Origin: *` и `Cross-Origin-Resource-Policy: cross-origin`.

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
    "methodologyVersion": "v2",
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
| categories[].weight | исходный вес категории в методике, указанной в analysis.methodologyVersion |
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
