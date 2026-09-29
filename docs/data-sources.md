# API и источники данных

Все данные собираются рабочими интерфейсами SourceCraft: REST API, Git и AppSec (CLI и API сканов). Готовых выгрузок сервис не использует. Что именно наблюдалось в живых ответах — [sourcecraft-capabilities.md](sourcecraft-capabilities.md).

## SourceCraft REST API

Базовый адрес — `https://api.sourcecraft.tech`. Запросы идут с сервисным токеном `SOURCECRAFT_TOKEN` для публичного каталога или с личным токеном пользователя, если он подключил SourceCraft в кабинете.

| Запрос | Зачем |
| --- | --- |
| `GET /repos` | глобальный публичный каталог (`SOURCECRAFT_DISCOVER_PUBLIC_REPOSITORIES=true`), до 100 страниц по 100 репозиториев. Из записи каталога берутся видимость, язык, лайки (`rating`) и последняя активность (`last_updated`) |
| `GET /orgs/{org}/repos` | тот же каталог по списку организаций (`SOURCECRAFT_PUBLIC_ORGANIZATIONS`) |
| `GET /me/repos` | личный каталог: всё, что видит токен пользователя, с пометкой видимости |
| `GET /repos/id:{id}` | карточка репозитория перед запуском: повторная проверка видимости и ветка по умолчанию |
| `GET /repos/{org}/{repo}/branches` | полный SHA коммита ветки по умолчанию: все анализаторы смотрят на одно состояние кода |
| `GET /repos/{org}/{repo}/issues` | Issues: открытые и закрытые задачи, их возраст и время закрытия |
| `GET /repos/{org}/{repo}/pulls` | Activity: merge requests и смерженные за период |
| `GET /repos/{org}/{repo}/releases` | Activity: релизы за период |
| `GET /repos/{org}/{repo}/contributors` | Activity: число участников |
| `GET /repos/{org}/{repo}/cicd/runs` | CI/CD: статусы, длительность и частота запусков ([cicd-analyzer.md](cicd-analyzer.md)) |

Ограничение частоты (`429`) и сбои SourceCraft не превращаются в нулевую оценку. Категория получает статус `error` («не удалось получить») или `unavailable` («нет данных») и в Score не участвует. Планировщик повторяет запуск с растущей задержкой.

## Git

Documentation и Code health читают файлы из временной рабочей копии. Её клонирует `git` по HTTPS с Git-адреса SourceCraft на зафиксированный коммит: без запроса пароля, без «ленивой» догрузки объектов, с пределами по числу файлов, объёму и времени. Activity читает из Git только даты коммитов за период. Рабочая копия удаляется после анализа; исходный код не сохраняется ни в базе, ни в отчёте — туда попадают только пути к файлам и номера строк. Пределы — в разделе «Масштабирование» [architecture.md](architecture.md).

## AppSec SourceCraft

Security строится только на результатах AppSec SourceCraft — собственного сканирования безопасности сервис не делает (п. 3.1 ТЗ).

- **Snapshot из CLI.** Отдельное задание вызывает `src appsec defect list` для публичных репозиториев и сохраняет обезличенную сводку: число открытых находок по движку (SAST, SCA, secrets) и критичности, статус исправления. Backend читает этот файл только на чтение и не старше `SOURCECRAFT_APPSEC_SNAPSHOT_MAX_AGE_SECONDS` (по умолчанию час). Регламент — [appsec-snapshot-refresh.md](appsec-snapshot-refresh.md).
- **API сканов** (`https://appsec.sourcecraft.tech`, `v1/scans/{id}`) — постраничный источник с подтверждённой полнотой ([sourcecraft-appsec-api.md](sourcecraft-appsec-api.md)).

Сводка без подтверждённой полноты даёт статус «мало данных», а не оценку ([security-analyzer.md](security-analyzer.md)).

## Яндекс ID

Только для входа: Authorization Code + PKCE через `https://oauth.yandex.ru/authorize` и `/token`, профиль (`id`, `login`) — `https://login.yandex.ru/info`. Токен Яндекса к SourceCraft доступа не даёт. Закрытые и внутренние репозитории открывает только личный токен SourceCraft, который пользователь подключает сам.

## Какая категория откуда берёт данные

| Категория | Источник | Метрики и формулы |
| --- | --- | --- |
| Security | AppSec: SAST, SCA, secrets | [security-analyzer.md](security-analyzer.md) |
| CI/CD | `cicd/runs` | [scoring-methodology.md § 4.1](scoring-methodology.md) |
| Documentation | файлы рабочей копии | [scoring-methodology.md § 4.4](scoring-methodology.md) |
| Activity | карточка репозитория, `pulls`, `releases`, `contributors`, даты коммитов из Git | [scoring-methodology.md § 3](scoring-methodology.md) |
| Issues | `issues` | [scoring-methodology.md § 2](scoring-methodology.md) |
| Code health | TODO и FIXME в комментариях исходников | [scoring-methodology.md § 4.3](scoring-methodology.md) |

Лайки и последняя активность нужны только рейтингу для сортировок. В Score они не входят.

## Внешние сервисы и AI

| Сервис | Для чего | Какие данные уходят |
| --- | --- | --- |
| SourceCraft API, Git, AppSec | единственный источник данных анализа | запросы с сервисным или личным токеном; сервис только читает |
| Яндекс ID | вход пользователя | стандартный OAuth-обмен; сервис получает `id` и `login` |
| Let's Encrypt (через Traefik) | TLS-сертификат стенда | только домен |

AI и LLM в решении не используются. Оценки, приоритеты и тексты рекомендаций — детерминированные правила по собранным фактам: одинаковые данные дают одинаковый отчёт. Исходный код, токены и данные закрытых репозиториев никуда за пределы SourceCraft не передаются. Внешних CDN, счётчиков и аналитики нет: шрифты и библиотеки интерфейса собраны в образ.

Лицензии открытых репозиториев соблюдаются так: код не копируется и не распространяется. Он читается во временной рабочей копии только для подсчёта признаков — наличия файлов, TODO и FIXME, — и удаляется после анализа.
