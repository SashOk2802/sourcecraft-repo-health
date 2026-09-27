# Frontend Repo Health

Интерфейс сервиса: рейтинг открытых репозиториев, отчёт по снимку анализа, вход через Яндекс ID с проверкой репозиториев из каталога сервиса и страница «Как считаем».

Стек: React 18, TypeScript, Vite и [Gravity UI](https://gravity-ui.com) — дизайн-система Яндекса, на которой сделан сам SourceCraft.

## Запуск

Вместе со всем проектом — `docker compose up` из корня репозитория, подробности в [docs/docker-development.md](../docs/docker-development.md).

Только frontend, без Docker (нужен Node.js 22 или новее):

~~~powershell
cd frontend
npm ci
npm run dev      # http://localhost:5173
npm test         # тесты vitest
npm run check    # проверка типов
npm run build    # проверка типов и сборка в dist/
npm run preview  # собранная версия на http://localhost:4173 с тем же proxy /api
~~~

## Откуда берутся данные

Раздел backend может быть недоступен: не настроен на сервере (503), его нет в этой версии или backend не отвечает. Чтобы стенд не показывал пустых экранов, у интерфейса три режима — переменная `VITE_DATA_SOURCE`:

| Режим | Что делает | Когда включается сам |
| --- | --- | --- |
| `auto` | Настоящий API. Если раздела нет (404 «Not Found», 405, 501), он не настроен (503) или backend не отвечает (502, 504, нет ответа), страница строится на демо-данных и помечена «Демо». Как только backend отдаст раздел, демо пропадёт само, без пересборки | `npm run build` и `npm run dev` рядом с backend (задан `API_PROXY_TARGET`, так делает compose) |
| `api` | Только настоящий API, ошибки показываются как есть | — |
| `demo` | Только демо-данные из `src/api/mocks/`, backend не нужен | одиночный `npm run dev` |

Настоящие ошибки демо не прячет: 404 конкретного анализа, 403 и 422 показываются как есть. Идентификаторы демо-данных начинаются с `demo-`, поэтому ссылку на демо-отчёт не спутать с настоящей. Демо-страницы закрыты от поисковиков (`noindex`), а репозитории и оценки в них вымышлены.

Задать режим явно:

~~~powershell
$env:VITE_DATA_SOURCE = "api"; npm run dev
~~~

Старая переменная `VITE_USE_MOCKS=true|false` тоже понимается (как `demo` и `api`).

В демо помогают параметры адреса:

- `?mock-user=1` — войти демо-пользователем;
- демо-анализ идёт около 10 секунд, запуски хранятся в sessionStorage вкладки.

| Адрес | Что показывает |
| --- | --- |
| `/` | рейтинг; `?preliminary=1` добавляет блок предварительных оценок |
| `/analyses/demo-1008` | отчёт: нет данных AppSec, оценка предварительная |
| `/analyses/demo-1012` | Score ограничен из-за критической уязвимости |
| `/analyses/demo-1006` | сразу все статусы категорий без оценки |
| `/analyses/demo-1021` | оценки нет: данных не получено |
| `/analyses/demo-0000` | отчёта нет (404) |
| `/me/repositories?mock-user=1` | список репозиториев и запуск анализа |
| `/methodology` | как считаем |

## Что откуда берётся

| Экран | Endpoint | На main |
| --- | --- | --- |
| Отчёт | `GET /api/v1/analyses/{id}/report` | есть; в production — Activity, Issues, CI/CD, Documentation и Code health, Security пока `unavailable` |
| Markdown | `GET /api/v1/analyses/{id}/report.md` | есть; демо-отчёт собирается в браузере в том же формате |
| Ход анализа | `GET /api/v1/analyses/{id}` — `queued`, `running`, `completed`, `partial`, `failed` с `error: {code, summary}` | есть |
| Запуск анализа | `POST /api/v1/repositories/{id}/analyses` | есть: после входа, по внутреннему id репозитория SourceCraft, только публичные репозитории из `SOURCECRAFT_PUBLIC_ORGANIZATIONS`; без неё и `SOURCECRAFT_TOKEN` — 503 |
| Методика | `GET /api/v1/methodology` | есть: веса, названия и лимит — с backend, объяснения и политика пересчёта — в `src/lib/methodologyTexts.ts` |
| Вход | `/api/v1/auth/yandex/start`, `/callback`, `GET /api/v1/me` (`{id, login}`), `POST /api/v1/auth/logout` | есть; без `YANDEX_CLIENT_ID` и `YANDEX_REDIRECT_URI` backend отвечает 503 — тогда в режиме `auto` работает демо-кабинет, в режиме `api` вход выключен с пояснением |
| Рейтинг | `GET /api/v1/leaderboard` | есть (#56); места и сортировки по [docs/leaderboard-policy.md](../docs/leaderboard-policy.md) |
| Мои репозитории | `GET /api/v1/me/repositories` | есть (#55): публичные репозитории из организаций `SOURCECRAFT_PUBLIC_ORGANIZATIONS`; последнего анализа в ответе пока нет. У backend без этого маршрута кабинет после входа предлагает проверить публичный репозиторий по идентификатору |
| Подключение SourceCraft | `/api/v1/connections/sourcecraft` | позже, для закрытых репозиториев; пока его нет, интерфейс его не предлагает |

Формат ответов описан в [docs/api-contract.md](../docs/api-contract.md); типы лежат в `src/api/*.ts`.

Открывать сервис нужно по адресу из `YANDEX_REDIRECT_URI`: запуск анализа и выход с другого адреса (например, `127.0.0.1` вместо `localhost`) отклонит защита от подделки запросов ([docs/csrf-protection.md](../docs/csrf-protection.md)) — интерфейс так и скажет.

## Выкладка на стенд

Для стенда есть отдельный образ: `Dockerfile.prod` собирает приложение и отдаёт его через nginx. Обычный `Dockerfile` остаётся для разработки — его запускает `compose.yaml` с Vite и перезагрузкой на лету.

Как и остальные контейнеры проекта, nginx в этом образе работает не от root: образ `nginxinc/nginx-unprivileged` запускается пользователем с uid 101 и слушает порт **8080**.

~~~bash
docker build -f frontend/Dockerfile.prod -t repo-health-frontend frontend
docker run -p 80:8080 -e API_PROXY_TARGET=http://backend:8000 repo-health-frontend
~~~

Что делает nginx (`nginx/default.conf.template`):

- `/api/` проксирует в `API_PROXY_TARGET` (по умолчанию `http://backend:8000`); имя резолвится на каждый запрос через `NGINX_RESOLVER` (по умолчанию `127.0.0.11` — DNS Docker), поэтому перезапуск backend не ломает proxy. Вне Docker задайте свой DNS;
- адреса страниц (`/analyses/…`, `/me/repositories`) отдают `index.html` — маршрутами занимается приложение;
- файлы сборки с хэшем в имени кэшируются на год, `index.html` всегда перепроверяется;
- gzip, заголовки безопасности, `GET /healthz` для healthcheck.

Режим данных задаётся при сборке: `--build-arg VITE_DATA_SOURCE=api`, по умолчанию `auto`.

С compose frontend для стенда подключается файлом-дополнением рядом с `compose.yaml` — сам `compose.yaml` при этом не меняется. Порты и тома dev-сервера заменяются, а не дописываются (`!override` и `!reset` понимает Docker Compose 2.24 и новее):

~~~yaml
# compose.stand.yaml
services:
  frontend:
    build:
      dockerfile: Dockerfile.prod
    ports: !override ["80:8080"]
    volumes: !reset []
~~~

~~~bash
docker compose -f compose.yaml -f compose.stand.yaml up -d --build
~~~

Если стенд всё же запускают на dev-сервере Vite, домен стенда нужно разрешить: `FRONTEND_ALLOWED_HOSTS=repo-health.example.ru` (или `all`), иначе Vite ответит «Blocked request».

## Структура

~~~text
src/
  api/          типы ответов, запросы к backend, выбор источника данных, демо-данные
  auth/         состояние входа через Яндекс ID
  components/   общие компоненты; report/ — части отчёта и ход анализа
  hooks/        загрузка данных, запуск анализа, мета-данные страницы
  lib/          форматирование по-русски, подписи статусов, полосы оценки, Markdown-отчёт
  pages/        страницы
  styles/       токены поверх Gravity UI и базовые стили
  theme/        выбор темы: как в системе, светлая или тёмная
  routes.ts     адреса страниц
  router.tsx    переходы без перезагрузки страницы
~~~

## Договорённости

- Frontend не считает Score и не решает, что отсутствие данных — ноль: все числа приходят от backend.
- «Нет данных», «мало данных», «не удалось получить» и «не применимо» показываются штриховкой и метками и не похожи на низкую оценку; у категории без данных в таблице стоит «не участвует в расчёте».
- Цвет — только там, где отклонение: ниже 60 — красный, 60–79 — жёлтый. Границы в `src/lib/scoreBands.ts`.
- Машинные коды причин (`reason`) переводятся в текст в `src/lib/reasonCodes.ts`; незнакомый код показывается как есть.
- Пути к API только относительные (`/api/...`): в разработке их проксирует Vite, на стенде — nginx.
- Отчёт всегда открывается по идентификатору снимка анализа, поэтому ссылка показывает один и тот же результат.
- Токен SourceCraft для закрытых репозиториев, когда backend добавит подключение, уходит на backend один раз и в браузер не возвращается; в демо-кабинете его не спрашивают.
- Классы по БЭМ: `block__element_modifier_value`, цвета — токены Gravity UI (`--g-color-*`) и свои `--rh-*` в `src/styles/tokens.css`.
- PDF — печать страницы отчёта: для неё есть отдельные стили, отчёт печатается светлым в любой теме.
- Вёрстка проверена на 390 px (телефон), 1024, 1280 и 1440 px: таблицы на узком экране становятся карточками.

## Тесты

Проверяем логику, а не вёрстку: разбор адресов и фильтров, форматирование чисел и дат, полосы оценки, коды причин, расчёт долей и потерь в отчёте, сильные и слабые стороны, места в рейтинге, ход анализа по этапам, выбор источника данных и Markdown-отчёт в формате backend. Запуск — `npm test`.
