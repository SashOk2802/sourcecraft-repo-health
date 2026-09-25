# Production-анализ публичных репозиториев SourceCraft

`POST /api/v1/repositories/{repositoryId}/analyses` запускает production worker,
когда одновременно заданы `SOURCECRAFT_TOKEN` и
`SOURCECRAFT_PUBLIC_ORGANIZATIONS`.

`SOURCECRAFT_PUBLIC_ORGANIZATIONS` — список slug организаций через запятую.
Resolver читает их каталог SourceCraft, ищет точный `repositoryId` и разрешает
анализ только при `visibility: public`. Даже сервисный токен с доступом к
private/internal репозиторию не расширяет права HTTP-клиента: такой запрос
получает `403`.

Перед созданием задания resolver запрашивает default branch и сохраняет её
полный неизменяемый commit SHA. Поэтому `analysis.commitSha` и файловые
анализаторы привязаны к одному состоянию кода, даже если ветка сдвинется во
время или после анализа.

Без полной пары переменных worker не создаётся, а endpoint отвечает `503`.
Это fail-closed режим до отдельного пользовательского подключения SourceCraft.
Оно потребуется для «Моих репозиториев» и любого анализа private/internal
репозитория.

В одном запуске provider регистрирует Activity, Issues, CI/CD, Documentation,
Code health и Security. CI/CD использует REST API. Security пока возвращает
`unavailable`: имеющийся AppSec CLI использует локальную сессию разработчика и
не запускается внутри веб-сервера.

Git-клон используется только Documentation и Code health. Он создаётся во
временной папке, после запуска удаляется, а токен передаётся в заголовке git,
не в URL. Backend image содержит `git` и запускается от непривилегированного
пользователя `app`.

REST-клиент SourceCraft создаётся с `trust_env=False`: proxy и подмена CA через
`HTTPS_PROXY`, `SSL_CERT_FILE` и другие переменные окружения не влияют на
запросы с Bearer PAT.
