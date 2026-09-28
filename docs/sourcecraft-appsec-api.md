# Постраничный источник AppSec

Ответственный: Артём Е. Зона: SourceCraft / Security.

Вход: `OWNER/REPOSITORY` и локально авторизованный SourceCraft CLI.
Выход: обезличенные агрегаты `severity/status/count`, полнота, commit и UUID скана от AppSec.
Зависимости: CLI 0.0.105, отдельный локальный профиль API и существующий snapshot
bridge. Общий `contracts.py` и web-worker не меняются.

Критерий этого этапа: получить все страницы непустого результата, проверить
полноту и commit, передать данные в существующий exporter. Числовая оценка
Security требует дополнительно подтвердить выполнение всех трёх движков.

## Что проверено 28 сентября 2026

Официальный CLI 0.0.105 обращается к `https://appsec.sourcecraft.tech`.
Для диагностики использовалась его локальная IAM-сессия. Сам CLI
`appsec defect list --json` отбрасывает метаданные пагинации.

Наблюдённые read-only методы:

| Метод | Параметры | Подтверждённый ответ |
| --- | --- | --- |
| `GET /v1/scans` | `gitRepo`, `pageSize`, `pageToken` | `data`, `totalSize`, `nextPageToken`; у scan есть `uuid`, `isLatest`, `scanType`, `commitHash`, `totalDefectGroups` |
| `GET /v1/scans/{uuid}` | `gitRepo` | `uuid`, `status=FINISHED`, `commitHash`, `gitRepo`, `timeFinished`, `isLatest`, `totalDefectGroups` |
| `GET /v1/defect-groups` | `gitRepo`, `scanUuid`, `type`, `pageSize`, `pageToken` | `data`, `totalSize`, `nextPageToken`; группа содержит `uuid`, `latestCommit`, `gitRepo`, `engineType`, `severity`, `status` |

`gitRepo` в запросе принимает UUID из публичного API репозитория. В ответах
AppSec это другой внутренний идентификатор: поставщик сравнивает его между
scan detail и группами, но не записывает в snapshot.

На тестовом репозитории получены три страницы SAST размером 10, 10 и 3,
всего 23 уникальные группы. Отдельная проверка итоговой сводки получила
6 HIGH, 12 MEDIUM и 5 LOW, все OPEN. SCA и Secrets вернули пустые списки.
Сырые группы, код, имена файлов, UUID находок и пользовательские реквизиты в
репозиторий не добавляются. В безопасный snapshot попадает только UUID самого
завершённого скана, чтобы ссылки отчёта открывали его результаты.

Числовые enum сопоставлены с именованным выводом официального CLI:
`engineType=3` → SAST, `severity=1/2/3` → LOW/MEDIUM/HIGH,
`status=0` → OPEN. Другие числовые значения не угадываются. Неизвестный
severity/status запрещает числовую оценку; неизвестный engine — ошибка источника.
Поэтому обработку непустых SCA/Secrets и других числовых enum ещё нужно
подтвердить реальными данными либо опубликованным контрактом.

## Ограничения источника

Эти методы **не входят в опубликованную REST-схему**. Поставщик включается
явным параметром; при изменении схемы возвращает безопасную ошибку.

Неизвестный `scanUuid` может молча вернуть последний scan. Поставщик выбирает
существующий последний `SCAN_TYPE_DEFAULT`, проверяет `FINISHED`, читает его
metadata до и после сбора, сверяет UUID, commit, время завершения и total.
Каждая непустая группа также обязана сообщить этот commit и внутренний repo id.
Это защищает от обычной смены последнего скана между страницами. API не
предоставляет подтверждённой транзакционной версии для изменений triage:
атомарный снимок статусов при одновременном редактировании не гарантируется.

Пустая страница с `totalSize=0` подтверждает отсутствие групп, но **не выполнение
конкретного движка**. Общий `FINISHED` также недостаточен. Поэтому пустые
SCA/Secrets остаются `unavailable`, без числа 0 и без выдуманного commit.
Сочетание полного SAST и недоступных SCA/Secrets даёт `insufficient_sample`
и `score=null`. Первый непустой источник уже работает; весь Security Score
пока нельзя считать завершённым.

Счётчики относятся к **группам дефектов**, как у прежнего CLI-адаптера.
Поле `findingsCount` не суммируется как число отдельных экземпляров.

## Локальная настройка

CLI устанавливается из [официального источника](https://sourcecraft.dev/portal/docs/ru/sourcecraft/operations/cli-quickstart).
Включите авторизацию `src auth login`. Активным должен оставаться обычный
профиль публичного API `ExtProd`.

Создайте локальный YAML вне отслеживаемых файлов проекта, например
`.local/sourcecraft-appsec-env.yaml`:

```yaml
environments:
  AppSecRead:
    public_api: https://appsec.sourcecraft.tech
    git: git.sourcecraft.dev
    ssh: ssh.sourcecraft.dev
    frontend: https://sourcecraft.dev
    iam: auth.yandex.cloud
    appsec: https://appsec.sourcecraft.tech
```

```sh
src envs import .local/sourcecraft-appsec-env.yaml
src --env AppSecRead auth login
python scripts/probe_sourcecraft_appsec.py OWNER/REPOSITORY --appsec-env AppSecRead
```

Профиль нужен для `src api`; стандартный AppSec CLI использует тот же хост.
Поставщик проверяет оба API URL до запросов к репозиторию. Токены не передаются
аргументами и не копируются в приложение. Без `--appsec-env` остаётся прежний
зонд с лимитом 100 и `completeness=unknown`.

Для экспорта на Linux добавьте `--appsec-env AppSecRead` к существующей команде
[snapshot bridge](security-analyzer.md#подключение-и-безопасность).
На Windows проверены сбор данных и безопасная сводка; POSIX-права exporter
проверяются Linux CI. Для snapshot не отключайте проверку прав ради Windows.

## Проверки и оставшееся

Тесты покрывают 201 группу, неодинаковые totals, повторные UUID/токены,
обрыв списка, пустую промежуточную страницу, лимиты, смену scan даже при том же
commit, чужие commit/repo, незавершённый scan, неизвестные enum, ошибки CLI,
отсутствие raw-данных и передачу полного SAST в snapshot bridge.

Для завершения Security Score нужен контракт результата **каждого** движка:
идентификатор скана, commit, выполнен/отключён/ошибка и полный набор групп.
Он должен позволять отличать успешно выполненный пустой SCA/Secrets от
отключённого сканера. Непустые SCA/Secrets также нужны для подтверждения enum.
