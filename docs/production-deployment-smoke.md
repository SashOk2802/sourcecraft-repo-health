# Проверка production-стенда

## Запуск production Compose

`compose.production.yaml` собирает frontend в статические файлы и запускает его
через непривилегированный nginx. Backend стартует без `--reload`, применяет миграции
перед запуском и доступен снаружи только через `/api` frontend-контейнера. Исходники
и Vite dev-server в этот Compose не монтируются и не запускаются.

На текущем стенде TLS завершает Traefik. Production Compose намеренно использует
обычное имя Docker-проекта из каталога `sourcecraft-repo-health`: так Traefik и
сервисы `frontend`/`backend` остаются в одной сети и могут обращаться друг к другу
по Docker DNS. Не запускайте production Compose с другим `--project-name`, пока не
подключили reverse proxy к отдельной общей сети.

Перед запуском задайте секреты только в окружении сервера или в локальном файле
`.env`, который исключён из Git:

```sh
docker compose -f compose.production.yaml config --quiet
docker compose -f compose.production.yaml up -d --build
```

Перед первым переключением подготовьте версионированный маршрут
`deploy/traefik/dynamic.yaml`. В нём frontend направлен на `frontend:8080` — это
порт nginx внутри контейнера, а не Vite `5173`. Скопируйте его в каталог конфигурации
Traefik **сразу после** запуска production Compose:

```sh
install -m 600 deploy/traefik/dynamic.yaml traefik/dynamic.yaml
```

При переключении между запуском Compose и обновлением `dynamic.yaml` допустима
короткая недоступность frontend. Traefik замечает изменение файла сам; его не нужно
перезапускать.

Обязательны `DATABASE_URL` и `POSTGRES_PASSWORD`. Для личного кабинета также нужны
настройки Яндекс ID и `SOURCECRAFT_CONNECTION_ENCRYPTION_KEY`. После настройки OAuth
задайте `VITE_YANDEX_AUTH=true` перед сборкой frontend; без этого флага кнопка входа
намеренно остаётся выключенной.

`SOURCECRAFT_TOKEN` нужен только для публичного каталога, рейтинга и периодического
пересчёта. Пока он не задан, личные подключения и анализы работают как обычно, а
public leaderboard/API безопасно отвечают `503`. Включайте одновременно token,
источник публичного каталога и `PUBLIC_ANALYSIS_SCHEDULER_ENABLED=true` только после
подготовки разрешённых публичных репозиториев.

Один token без `SOURCECRAFT_PUBLIC_ORGANIZATIONS` и без
`SOURCECRAFT_DISCOVER_PUBLIC_REPOSITORIES=true` не включает каталог и не должен
ломать запуск: это состояние personal-only стенда до подготовки публичного рейтинга.
PAT и ключ Fernet нельзя добавлять в Compose, логи или pull request.

## Выпуск и откат

1. После merge в `main` получите чистый конкретный коммит и создайте для него
   аннотированный тег `demo-YYYYMMDD-N`.
2. На сервере выполните `git pull --ff-only origin main`; не перезаписывайте
   локальные `compose.prod.yaml`, `traefik/` и `.env`.
3. Перед миграциями создайте проверяемый backup PostgreSQL в каталоге с правами
   только владельца. Не используйте `docker compose down -v`:

   ```sh
   umask 077
   mkdir -p /var/backups/repo-health
   docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
     > /var/backups/repo-health/repo-health-$(date -u +%Y%m%dT%H%M%SZ).sql
   test -s /var/backups/repo-health/repo-health-*.sql
   ```

4. Запустите production Compose и сразу выполните внешний smoke из следующего
   раздела. Зафиксируйте hash развернутого коммита и время выкладки в журнале команды.
5. При P0-ошибке вернитесь на предыдущий проверенный тег через `git checkout <tag>`,
   поднимите тот же production Compose и восстановите БД **только** если миграция
   действительно изменила данные. До подтверждения причины backup не удаляйте.

Репозиторный CI проверяет сборку и чистый Docker Compose, но не подтверждает, что
публичный домен действительно обслуживает production frontend и настроенный backend.
Для проверки после выкладки добавлен ручной workflow `Demo deployment smoke`.

## Настройка

Создайте GitHub Actions variable `DEMO_BASE_URL` с корневым HTTPS URL стенда. URL
не хранится в workflow и не выводится скриптом. Допускается только HTTPS без userinfo,
query, fragment, дополнительного path и нестандартного порта. Локальные адреса
отклоняются.

Запустите workflow вручную после выкладки. Он использует только публичные запросы без
cookie и токенов. Checkout credentials удаляются до выполнения кода репозитория.

Локально ту же проверку можно выполнить так:

```sh
python scripts/check_production_deployment.py https://repo-health.example
```

## Что проверяется

- корневая страница отвечает HTML без ссылок на Vite dev client и исходный TSX;
- Vite client и дерево `src` не выдаются как JavaScript/TypeScript;
- на странице есть CSP, HSTS, `nosniff`, Referrer-Policy и Permissions-Policy;
- `/api/v1/health` возвращает точный безопасный health payload;
- публичные methodology и leaderboard доступны как JSON;
- `/api/v1/me` и `/api/v1/me/repositories` без сессии отвечают `401` с `no-store`;
- redirect не выполняются, proxy-переменные окружения не используются.

При ошибке выводятся только стабильные коды проверок. URL, тела ответов и тексты
сетевых исключений в CI-лог не попадают.
