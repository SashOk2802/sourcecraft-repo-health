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

Production web-worker пока не запускает локальный `src` CLI и честно возвращает
`unavailable`. Следующая интеграционная задача — подтверждённый постраничный
AppSec-поставщик с минимальными правами и привязкой scan к анализируемому
commit. Только он сможет передать `completeness = complete`.

## Проверки

Тесты покрывают: отсутствие данных, неполную выборку, пустой полный scan,
confirmed critical и глобальный cap, untriaged critical, false positive,
ограничение повторяющихся severity, рекомендации, отсутствие утечек и
прохождение результата через JSON/Markdown-отчёт.
