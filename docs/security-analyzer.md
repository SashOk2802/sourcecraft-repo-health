# Security Score v1

`backend/app/analyzers/security.py` превращает безопасную агрегированную
сводку AppSec SourceCraft в `CategoryResult` категории `security`. Он не
запускает собственный security-сканер и не сохраняет сырые finding'и.

## Какие данные принимает анализатор

Поставщик получает `RepositoryRef` и возвращает `SecurityFacts`. Сводка должна
содержать ровно три движка: `SAST`, `SCA`, `SECRETS`. Для каждого движка
разрешены только следующие поля:

```json
{
  "engine": "SAST",
  "availability": "available",
  "finding_count": 3,
  "severities": ["HIGH", "MEDIUM"],
  "reason": null,
  "finding_groups": [
    {"severity": "HIGH", "status": "OPEN", "count": 1},
    {"severity": "MEDIUM", "status": "TRIAGED_TP", "count": 2}
  ],
  "completeness": "complete"
}
```

Группа хранит только severity, status и количество. В ней нет имени
репозитория, идентификаторов finding'ов, путей файлов, текста правила,
фрагмента кода или значения секрета.

Неизвестный severity/status, отсутствие группы или `completeness = unknown`
не получают придуманного штрафа: анализатор возвращает `insufficient_sample`.

## Статусы результата

| Условие | Статус | Причина | Score |
| --- | --- | --- | --- |
| Все AppSec-результаты отсутствуют | `unavailable` | `appsec_unavailable` | `null` |
| Ошибка поставщика | `error` | `appsec_source_error` | `null` |
| Есть ответ, но не подтверждены полнота или схема | `insufficient_sample` | `appsec_coverage_not_confirmed` | `null` |
| Три полных результата с известными группами | `measured` | `security_score_v1` | 0–100 |

Текущий локальный CLI-зонд использует `--limit 100` и ещё не подтвердил
постраничный контракт. Поэтому он всегда возвращает `completeness = unknown`:
его живые данные можно показать как доказательство интеграции, но они не могут
стать числовой Security-оценкой до появления полного поставщика.

## Формула и ограничение общего Score

Открытыми считаются findings со статусом `OPEN` или `TRIAGED_TP`.

```text
penalty = 60 * min(critical, 2)
        + 15 * min(high, 3)
        +  5 * min(medium, 4)
        +  1 * min(low, 10)

SecurityScore = max(0, 100 - penalty)
```

`INFO`, `RESOLVED_FP` и `RESOLVED_TOLERABLE` не уменьшают оценку. Количества
в формуле ограничены, чтобы множество однотипных слабых findings не могло
непропорционально изменить Repo Health Score.

Если есть critical finding со статусом `TRIAGED_TP`, общий scoring engine
автоматически применяет ограничение `RepoHealthScore = min(Score_base, 60)`.
Неподтверждённый `OPEN` critical ухудшает Security Score, но не включает
глобальное ограничение до подтверждения true positive.

## Рекомендации и доказательства

При открытых critical/high/medium/low findings формируются рекомендации
приоритетов P0/P1/P2/P3. Они ссылаются только на агрегированный источник
`sourcecraft-appsec/appsec-defects`; будущий интерфейс может предложить
пользователю открыть finding в SourceCraft в его собственном контексте доступа.

## Подключение и безопасность

`make_analyzer(provider)` создаёт функцию для
`AnalyzerRegistration("security", ...)`. Поставщик вызывается на каждый
запуск; сырые факты не кэшируются внутри анализатора. Исключение поставщика
превращается в безопасную фиксированную ошибку без текста исключения в логе.

Production web-worker никогда не запускает локальный `src` CLI с IAM-сессией
пользователя. Вместо этого есть безопасный **snapshot bridge**:

1. На машине, где пользователь уже выполнил `src auth login`, запускается
   exporter. Он вызывает SourceCraft CLI, отбрасывает raw findings и атомарно
   записывает только разрешённые агрегаты в отдельный каталог. Перед первым
   запуском задайте GID группы, через которую контейнер сможет **только
   читать** файл. Для локального Linux проще всего использовать свою основную
   группу — это не требует `sudo`:

   ```bash
   export SOURCECRAFT_APPSEC_SNAPSHOT_HOST_DIR="$PWD/.local/sourcecraft-appsec-snapshots"
   export SOURCECRAFT_APPSEC_SNAPSHOT_READER_GID="$(id -g)"
   mkdir -p "$SOURCECRAFT_APPSEC_SNAPSHOT_HOST_DIR"
   ```

   Затем создайте snapshot тем же GID:

   ```bash
   python scripts/export_sourcecraft_appsec_snapshot.py OWNER/REPOSITORY \
     --output-dir "$SOURCECRAFT_APPSEC_SNAPSHOT_HOST_DIR" \
     --reader-gid "$SOURCECRAFT_APPSEC_SNAPSHOT_READER_GID" \
     --src-bin /absolute/path/to/src
   ```

   Идентификатор репозитория не вводится вручную: exporter читает его через
   `src api` для того же `OWNER/REPOSITORY`. Commit также не передаётся
   аргументом. Каждый доступный AppSec-движок обязан сообщить одинаковый
   корректный `latestCommit`. Пустой ответ `[]` этого commit не содержит и
   поэтому не считается результатом для текущего commit, даже если известен
   head default-ветки. Если другой движок вернул findings с commit, exporter
   сохранит только эту связанную часть; пустой ответ попадёт в snapshot как
   `sourcecraft_appsec_commit_unavailable`, а Security останется
   `insufficient_sample`. Если все ответы пустые и commit неизвестен, snapshot
   не создаётся и Security остаётся `unavailable`.
   Snapshot'ы прежней схемы необходимо создать заново: worker отклоняет их,
   поскольку они могли быть привязаны к head ветки без commit от AppSec.

2. В стандартном `compose.yaml` этот каталог уже монтируется в `backend` как
   `/run/sourcecraft-appsec:ro`. Docker добавляет backend-пользователя `app`
   только в группу `SOURCECRAFT_APPSEC_SNAPSHOT_READER_GID`; exporter создаёт
   каталог с правами `0750` и JSON с `0640`. Поэтому backend может прочитать
   snapshot, но не изменить файл или каталог хоста. После export запустите
   Compose в том же shell, чтобы он увидел обе переменные из шага 1:

   ```bash
   docker compose up --build
   ```

Snapshot привязан SHA-256 отпечатком к `repository.id`, к конкретному commit и
временем создания. Файл старше заданного интервала, от другого repository/commit,
symlink, слишком большой или с неизвестной схемой не используется. Корневой
каталог exporter'а нельзя делать group/world-writable, а worker не следует
symlink'ам ни каталога, ни файла. Отсутствующий или устаревший snapshot — это
`unavailable`, испорченный — безопасная ошибка без вывода содержимого.

Так в демонстрации используются реальные данные SourceCraft, но ни токен, ни
локальная IAM-сессия, ни сырые findings не входят в контейнер приложения. Текущий
CLI всё ещё не подтверждает постраничный обход: его exporter передаёт
`completeness = unknown`, поэтому такая реальная сводка остаётся
`insufficient_sample` и не получает ложный числовой Score. Будущий
подтверждённый постраничный API-поставщик сможет записать тот же контракт с
`completeness = complete` и включить Security Score без изменений анализатора.

## Проверки

Тесты покрывают: отсутствие данных, неполную выборку, пустой полный scan,
confirmed critical и глобальный cap, untriaged critical, false positive,
ограничение повторяющихся severity, рекомендации, отсутствие утечек,
snapshot с привязкой к commit/свежести, вывод repository id и commit из
SourceCraft, запрет symlink/слишком больших файлов и прохождение результата
через JSON/Markdown-отчёт.
