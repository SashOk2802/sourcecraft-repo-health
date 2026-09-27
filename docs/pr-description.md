# PR: Анализ файлов — категория Code health (ревью V.1 + V.2)

## Что изменилось

Добавлен анализ файлов (категория Code health): сырые счётчики
(`total_analyzed_files`, `todo_count`, `fixme_count`), формула плотности
технического долга и рекомендации. Методика зафиксирована в
[`docs/scoring-methodology.md`](scoring-methodology.md) §4.3.

### Пол знаменателя плотности

Знаменатель — `max(total_files, 10)` (`DENSITY_MIN_FILES`). Один FIXME даёт
50 баллов и в репозитории из одного файла, и из десяти; на 100 файлах score
равен 95. Решение записано в
[`docs/methodology-owner-approval.md`](methodology-owner-approval.md) и
покрыто `test_code_health_small_repo_single_marker_density_semantics`.

### Порог «критичного FIXME» (P1)

`FIXME_CRITICAL_COUNT = 2` остаётся: один FIXME — P2, с двух включительно —
P1. Это проверяет `test_code_health_fixme_critical_count_boundary`.

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