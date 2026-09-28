# Production-анализ репозиториев SourceCraft

`POST /api/v1/repositories/{repositoryId}/analyses` запускает production worker
от имени пользователя, вошедшего через Яндекс ID. Если пользователь подключил
SourceCraft, backend применяет его зашифрованное server-side подключение. Без
личного подключения доступен только public fallback. Для него нужен
`SOURCECRAFT_TOKEN` и ровно один режим каталога:

- `SOURCECRAFT_DISCOVER_PUBLIC_REPOSITORIES=true` читает официальный глобальный
  `GET /repos`;
- `SOURCECRAFT_PUBLIC_ORGANIZATIONS` ограничивает каталог списком slug
  организаций через запятую.

Глобальный обход запрашивает не больше 100 страниц по 100 репозиториев и задаёт
`sort_by=created_at`, чтобы изменение рейтинга не приводило к пропускам или
дубликатам между страницами. SourceCraft не гарантирует snapshot каталога:
изменение visibility или удаление во время обхода может повлиять на следующие
страницы. Повторный проход планировщика сверяет каталог заново. Если лимит страниц
исчерпан либо API вернул дубликат или непубличную запись, backend отклоняет весь
результат, а не показывает неполный список.

При запуске анализа глобальный режим получает один репозиторий через
`GET /repos/id:{repo_id}`, а allowlist-режим ищет его в разрешённых организациях.
Оба режима повторно требуют `visibility: public`. Даже сервисный токен с доступом
к private/internal репозиторию не расширяет права HTTP-клиента: такой запрос
получает `403`.

Перед созданием задания resolver запрашивает default branch и сохраняет её
полный неизменяемый commit SHA. Поэтому `analysis.commitSha` и файловые
анализаторы привязаны к одному состоянию кода, даже если ветка сдвинется во
время или после анализа.

Личное подключение использует `SOURCECRAFT_CONNECTION_ENCRYPTION_KEY`. PAT
хранится только как ciphertext, привязан к `user.id` Яндекс-сессии и
расшифровывается на время конкретного SourceCraft API или Git-вызова. Если
личного подключения нет и репозиторий не разрешён public-каталогом, endpoint
отвечает `409`. Если ни personal, ни public режим нельзя настроить, запуск
остаётся fail-closed.

В одном запуске provider регистрирует Activity, Issues, CI/CD, Documentation,
Code health и Security. CI/CD использует REST API. Security не запускает
локальный SourceCraft CLI в web-worker: IAM-сессия пользователя и raw findings
не должны попадать в контейнер приложения.

Для демонстрации реальных AppSec-данных предусмотрен необязательный безопасный
snapshot bridge. Локальный exporter, запущенный после `src auth login`, оставляет
в отдельном каталоге только группы `severity/status/count`. Compose монтирует
`SOURCECRAFT_APPSEC_SNAPSHOT_HOST_DIR` в backend как read-only
`/run/sourcecraft-appsec:ro`. Exporter назначает файлу права `0640` и каталогу
`0750` для GID `SOURCECRAFT_APPSEC_SNAPSHOT_READER_GID`, а backend получает
только членство в этой группе. Так разные Unix-пользователи могут передать
snapshot без world-readable прав и без возможности backend менять данные хоста.
`SOURCECRAFT_APPSEC_SNAPSHOT_MAX_AGE_SECONDS` ограничивает срок свежести
(по умолчанию 3600 секунд).

Exporter сам получает repository id через SourceCraft API. Каждый доступный
AppSec scan принимается только с единым `latestCommit` от AppSec-источника.
Пустой ответ `[]` не содержит commit, поэтому отмечается как недоступный для
текущего снимка. Если других привязанных результатов нет, snapshot не создаётся.
Snapshot'ы прежней схемы отклоняются и требуют повторного экспорта.
Snapshot обязан совпасть по SHA-256 отпечатку repository id и по commit SHA
задания. Неверный, старый, слишком большой файл или symlink не используется;
корневой каталог и файл не могут быть symlink'ами. Без свежего snapshot'а
Security возвращает `unavailable`.

CLI пока подтверждает только ограниченную выборку последнего скана, а не
постраничный полный набор. Поэтому exporter сохраняет `completeness=unknown`:
реальный результат виден в отчёте как `insufficient_sample`, но не получает
придуманный Security Score. Полный Score станет возможен только после
подтверждения постраничного AppSec API или другого полного контракта источника.
Пошаговая команда exporter'а и compose mount описаны в
[документе Security-анализатора](security-analyzer.md).

Git-клон используется только Documentation и Code health. Он создаётся во
временной папке, после запуска удаляется, а токен передаётся в заголовке git,
не в URL. До checkout проверяются лимиты числа файлов, размера blob и дерева;
во время clone/fetch watchdog ограничивает общий workspace и завершает дерево
Git-процессов. Backend image содержит `git` и запускается от
непривилегированного пользователя `app`.

REST-клиент SourceCraft создаётся с `trust_env=False`: proxy и подмена CA через
`HTTPS_PROXY`, `SSL_CERT_FILE` и другие переменные окружения не влияют на
запросы с Bearer PAT.


## Регулярный пересчёт public-каталога

Для периодического обновления рейтинга задайте `PUBLIC_ANALYSIS_SCHEDULER_ENABLED=true` после применения миграций PostgreSQL. Планировщик читает тот же настроенный public-каталог, создаёт ограниченную пачку заданий через production-dispatcher и не использует данные browser-сессий или пользовательские токены. Если PostgreSQL, public-каталог или production-dispatcher не сконфигурированы, явное включение завершит запуск с ошибкой вместо небезопасного fallback.

Подробности об интервалах, retry и координации экземпляров: [политика регулярного пересчёта](scheduling-policy.md).
