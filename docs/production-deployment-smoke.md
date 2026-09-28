# Проверка production-стенда

## Запуск production Compose

`compose.production.yaml` собирает frontend в статические файлы и запускает его
через непривилегированный nginx. Backend стартует без `--reload`, применяет миграции
перед запуском и доступен снаружи только через `/api` frontend-контейнера. Порт
frontend привязан к loopback хоста, чтобы TLS завершал внешний reverse proxy.

Перед запуском задайте секреты только в окружении сервера или в локальном файле
`.env`, который исключён из Git:

```sh
docker compose -f compose.production.yaml config --quiet
docker compose -f compose.production.yaml up -d --build
```

Обязательны `DATABASE_URL`, `POSTGRES_PASSWORD` и `SOURCECRAFT_TOKEN`. Для личного
кабинета также нужны настройки Яндекс ID и `SOURCECRAFT_CONNECTION_ENCRYPTION_KEY`.
PAT и ключ Fernet нельзя добавлять в Compose, логи или pull request.

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
