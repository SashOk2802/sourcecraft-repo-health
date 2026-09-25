# PR: Анализ файлов — категория Code health (ревью V.1 + V.2)

## Что изменилось

Добавлен анализ файлов (категория Code health): сырые счётчики
(`total_analyzed_files`, `todo_count`, `fixme_count`), формула плотности
технического долга и рекомендации. Методика зафиксирована в
[`docs/scoring-methodology.md`](scoring-methodology.md) §4.3.

### Правило малых репозиториев (ревью V.1)

Поведение **задокументировано** в `docs/scoring-methodology.md` §4.3
(«Правило малых репозиториев») и **покрыто регрессионным тестом**
`test_code_health_small_repo_single_marker_density_semantics` в
`backend/tests/test_file_analyzers.py`: штраф растёт как `1 / total_files`,
поэтому одинокий FIXME обнуляет категорию при `total_files <= 5`
(penalty ≥ 100), одинокий TODO — при `total_files = 1`. Интерпретация —
«концентрированный долг в маленьком репо хуже», а не дефект нормировки.

Тест и документация доказывают **стабильность и воспроизводимость** поведения,
но не корректность самого порога: значение `total_files <= 5` помечено
`PENDING_APPROVAL` в §4.3 и **ожидает согласования владельцем методики**
(см. [`docs/methodology-owner-approval.md`](methodology-owner-approval.md)).

### Порог «критичного FIXME» (P1) (ревью V.2)

Поведение `FIXME_CRITICAL_COUNT = 2` (`backend/app/analyzers/code_health.py`)
задокументировано в `docs/scoring-methodology.md` §4.3 и **покрыто регрессионным
тестом** `test_code_health_fixme_critical_count_boundary` в
`backend/tests/test_file_analyzers.py`: единичный FIXME — P2, от двух
(граница константы включительно) — P1. Уровень уверенности тот же, что и для
правила малых репозиториев: значение порога тоже помечено `PENDING_APPROVAL`
и ожидает решения владельца методики.

## Контракт и зависимости

- [x] Общий контракт не менялся.
- [ ] Общий контракт изменился и согласован с затронутыми модулями.
- [ ] Новая внешняя интеграция или её ограничение описаны в документации.

## Проверка

- `python -m pytest` — весь набор тестов, включая
  `backend/tests/test_file_analyzers.py::FileAnalyzersTest::test_code_health_small_repo_single_marker_density_semantics`
  и `backend/tests/test_file_analyzers.py::FileAnalyzersTest::test_code_health_fixme_critical_count_boundary`.
- `ruff check backend --ignore EXE002`.

## Для ревью

- [ ] Изменение ограничено одной задачей.
- [ ] Секреты, токены и данные закрытых репозиториев не добавлены в код или логи.
- [ ] Если изменялась категория, описаны входные факты, формула и сценарий отсутствия данных.