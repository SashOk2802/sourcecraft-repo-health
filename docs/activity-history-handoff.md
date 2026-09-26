# Handoff: git-история (`commit_history`) для категории Activity

**Кому:** владелец категории Activity (@eReem0)
**От:** владелец модуля доступа к Git (`LocalGitRepository`)
**Статус:** справочная заметка о готовом контракте. В v1 Activity **не вызывает**
`commit_history()` — частота коммитов и активные недели по Git не измеряются
(см. [`docs/scoring-methodology.md`](scoring-methodology.md) §3). Этот документ
фиксирует контракт заранее, чтобы будущее подключение не изобретало заново
семантику дат, обрезки выборки и статусов.

Исходная договорённость о владении зафиксирована в
[`docs/backend-team-roles.md`](backend-team-roles.md): «Участник 1 владеет
модулем доступа к Git и файлам. Участник 3 использует его для анализа истории
коммитов».

---

## Что даёт `commit_history()` / `HistoryResult`

Код: [`backend/app/integrations/git_repository.py`](../backend/app/integrations/git_repository.py)

```python
from datetime import UTC, datetime

result: HistoryResult = repository.commit_history(
    since=datetime(2026, 3, 1, tzinfo=UTC),  # опционально: левая граница окна
    until=datetime(2026, 9, 1, tzinfo=UTC),  # опционально: правая граница окна
    max_commits=10_000,                       # DEFAULT_MAX_HISTORY_COMMITS
)
```

### `HistoryResult`

| Поле | Тип | Смысл |
| --- | --- | --- |
| `commits` | `tuple[CommitFact, ...]` | Коммиты первой родительской линии (`--first-parent`) — основная линия ветки, без внутренних правок влитых PR |
| `truncated` | `bool` | `True`, если выборка достигла `max_commits` (по умолчанию 10 000) — история была обрезана |

### `CommitFact`

| Поле | Тип | Смысл |
| --- | --- | --- |
| `committed_at` | `datetime` (UTC) | **Commiter-дата** коммита (не авторская!) |

### Семантика дат (важно)

- Команда выполняется с `--format=%ct` — **committer date**.
- Фильтры `--since`/`--until` в git тоже работают по committer date.
- Поэтому возвращаемые `committed_at` согласованы с окном запроса: коммит с
  авторской датой вне окна, но committer-датой внутри, будет возвращён (с
  committer-датой), и наоборот. Используйте только `committed_at`; авторская
  ось (`%at`) не возвращается намеренно — это исправление GH.1.
- Все метки приводятся к UTC.

---

## Ограничение shallow-клона (глубиной управлять не нужно)

- [`LocalGitRepository.clone()`](../backend/app/integrations/git_repository.py)
  всегда клонирует `--depth 1` (shallow) — осознанное архитектурное решение
  ради скорости (см. [`docs/architecture-proposal.md`](architecture-proposal.md)).
- `commit_history()` **сам догружает историю** при необходимости через
  `_ensure_history_depth()`: `fetch --shallow-since <since>`, если задано окно,
  либо `fetch --unshallow` без окна.
- **Activity не должна менять `clone()` на полный клон** и не обязана знать про
  глубину: достаточно вызвать `commit_history()` и корректно обработать флаг
  `truncated`.
- Стоимость догрузки оплачивается за вызов; бюджет размера выборки —
  `max_commits`, бюджет времени — таймаут git-команд `timeout_seconds`
  (по умолчанию 60 секунд).

---

## Рекомендуемый контракт для метрик «активные недели»

Повторяет уже существующий паттерн честного отказа в этом репозитории
(`code_health_scan_limit_exceeded`, обрезанные источники у Issues/Activity,
CI/CD) — новый статус вводить не нужно:

| Условие | Поведение |
| --- | --- |
| Полная история (`truncated=False`) | `measured` — считать частоту/активные недели по `committed_at` |
| Выборка оборвана (`truncated=True`) | `insufficient_sample`, `score=None` — честный отказ, а не частичный заниженный балл |

Если для заданного окна история пуста — решить, применима ли метрика
(аналогично пустому списку MR у Activity: метрика выпадает из расчёта, а не
даёт ноль).

---

## Проверено тестами

`commit_history()` покрыт регрессионными тестами в
[`backend/tests/test_git_repository.py`](../backend/tests/test_git_repository.py):

- `test_commit_history_uses_committer_dates_matching_since_until` — committer-ось
  согласована с фильтром (GH.1);
- `test_commit_history_flags_truncation_at_limit` /
  `test_commit_history_not_truncated_below_limit` — флаг `truncated` на границе
  `max_commits` (GH.2).

Запуск: `python -m pytest backend/tests/test_git_repository.py -q`.

## Критерии готовности подключения в Activity

1. Читать `HistoryResult.truncated` и при `True` отдавать `insufficient_sample`
   (`score=None`), а не частичный низкий балл.
2. Группировать недели по `committed_at` (committer-ось), не смешивая с
   авторскими датами.
3. Не менять `clone()`: углубление — внутренняя ответственность
   `commit_history()`.
4. Покрыть новые метрики/рекомендации тестами по образцу существующих тестов
   категории Activity (`backend/tests/test_activity_analyzer.py`).