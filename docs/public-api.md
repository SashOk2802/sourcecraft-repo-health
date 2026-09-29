# Публичный API Repo Health

Публичный API - дополнительная возможность проекта: он позволяет встроить
актуальную оценку публичного репозитория SourceCraft в сайт, дашборд или бота.
Это read-only API без ключа и без пользовательской сессии.

## Быстрый старт

Для репозитория `team/platform-api` запросите:

```bash
curl "https://alhamdulylia.ru/api/v1/public/repositories/team/platform-api/health"
```

Пример ответа:

```json
{
  "repository": {
    "organizationSlug": "team",
    "repositorySlug": "platform-api",
    "url": "https://sourcecraft.dev/team/platform-api",
    "language": "Python"
  },
  "score": 82.5,
  "coverage": 1,
  "isPreliminary": false,
  "scoreLimited": false,
  "analyzedAt": "2026-09-29T12:00:00Z",
  "methodologyVersion": "v2",
  "categories": [
    { "code": "security", "label": "Безопасность", "status": "measured", "score": 90 }
  ]
}
```

Для браузерного приложения достаточно обычного `fetch`: endpoint отдаёт
`Access-Control-Allow-Origin: *`.

```js
const url = "https://alhamdulylia.ru/api/v1/public/repositories/team/platform-api/health";
const response = await fetch(url);
if (!response.ok) throw new Error(`Repo Health API: ${response.status}`);
const health = await response.json();
```

## Поля и интерпретация

- `score` - итог от 0 до 100 или `null`, если нет измеримых категорий.
- `coverage` - доля веса категорий, для которых получены измеримые данные.
- `isPreliminary` - `true`, если покрытие неполное. Такой результат нельзя
  подавать как окончательный рейтинг.
- `scoreLimited` - итог ограничен критической подтверждённой проблемой Security.
- `categories` - короткие оценки категорий. `status` объясняет, измерена ли
  категория; `unavailable` и `error` не означают ноль баллов.
- `methodologyVersion` фиксирует версию расчёта. Формула и веса доступны по
  `GET /api/v1/methodology`.

API не возвращает подробный отчёт анализа: он может быть персональным, а также
содержать рекомендации и подтверждающие факты. Для публикации оценки используйте
только этот endpoint или SVG badge.

## Доступ, ошибки и кеширование

| HTTP | Значение |
| --- | --- |
| `200` | Найден текущий снимок репозитория, подтверждённого public-каталогом. |
| `404` | Репозиторий отсутствует, не public или для текущей методики ещё нет снимка. Эти причины намеренно не различаются. |
| `503` | Не настроен или временно недоступен каталог SourceCraft. Повторите запрос позднее. |

Успешный ответ можно кешировать пять минут: сервер отдаёт
`Cache-Control: public, max-age=300, s-maxage=300`. Ответы `404` и `503` всегда
получают `Cache-Control: no-store`: видимость и наличие свежего анализа могут
измениться.

## Безопасное использование

Endpoint работает только с public-репозиториями. Не передавайте в URL или
заголовках PAT SourceCraft, cookie Яндекс ID либо идентификатор личного анализа -
они этому API не нужны. После перевода репозитория в private следующий ответ
не раскроет ранее сохранённую оценку: видимость повторно проверяется по каталогу
SourceCraft.

Для README есть отдельный безопасный SVG badge:

```text
GET /api/v1/repositories/{organization_slug}/{repository_slug}/badge.svg
```
