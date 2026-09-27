# Handoff: git-история для категории Activity

**Кому:** владелец категории Activity
**От:** владелец модуля доступа к Git (`LocalGitRepository`)
**Статус:** контракт готов и покрыт тестами. Activity в v1 его не вызывает.

Частота коммитов и активные недели по Git не входят в оценку Activity
(`docs/scoring-methodology.md` §3). Этот документ фиксирует семантику заранее,
чтобы подключение не выбирало авторскую дату и не принимало обрезанную историю
за полную.

## Вызов

```python
result = repository.commit_history(
    since=datetime(2026, 3, 1, tzinfo=UTC),
    until=datetime(2026, 9, 1, tzinfo=UTC),
    max_commits=10_000,
)
```

| Поле | Смысл |
| --- | --- |
| `commits` | Первая родительская линия (`--first-parent`), без внутренних коммитов влитых PR |
| `commits[].committed_at` | Committer-дата в UTC. Авторская дата не возвращается |
| `truncated` | `True`, если выборка достигла `max_commits` |

`--since` и `--until` в git тоже фильтруют по committer date, поэтому
`committed_at` лежит на той же оси, что и запрошенное окно.

## Shallow-клон

`clone()` по-прежнему делает `--depth 1`. Углублять его для документации и
code health не нужно: эти категории `commit_history()` не вызывают.

Сам метод догружает историю, только если в клоне есть `.git/shallow`:

| Вызов | Команда |
| --- | --- |
| Задан `since` | `fetch --shallow-since <since>` |
| `since` не задан | `fetch --unshallow` |

Вызов без окна на большом репозитории может упереться в таймаут git (по
умолчанию 60 секунд). Для активных недель передавайте `since` из периода
анализа.

## Как подключать метрику

| Условие | Поведение |
| --- | --- |
| `truncated=False` | Можно считать частоту и активные недели по `committed_at` |
| `truncated=True` | `insufficient_sample`, `score=None` |
| История в окне пуста | Метрика выпадает из расчёта, как пустой список MR, и не даёт ноль |

`clone()` при подключении не менять.

## Проверка

`backend/tests/test_git_repository.py` проверяет committer-ось на локальном
репозитории, флаг `truncated`, оба режима догрузки shallow-клона и то, что
`activity.py` не содержит вызова `commit_history(`.
