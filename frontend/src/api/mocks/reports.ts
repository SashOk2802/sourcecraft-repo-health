import { getScoreBand, type ScoreBand } from "../../lib/scoreBands";
import type { CategoryStatus, Evidence, RecommendationPriority } from "../common";
import type { CategoryMetric, Recommendation, ReportCategory, RepositoryReport } from "../report";
import {
  findMockRepositoryByAnalysisId,
  mockAnalysisId,
  mockRepositories,
  type MockCategoryCode,
  type MockRepository,
} from "./catalog";
import { findRun, finishedAt, MOCK_ANALYSIS_DURATION_MS } from "./runs";
import { scoreMockCategories, type MockScoredCategory } from "./scoring";
import { mockSession } from "./session";
import { minutesAgo } from "./time";

/*
 * Mock-отчёты в формате docs/api-contract.md. Подробно расписаны четыре случая:
 * - gorod-dev/transit-api — нет данных AppSec, оценка предварительная;
 * - lastochka/android-sdk — критическая уязвимость ограничивает Score;
 * - edu-kit/olympiad-judge — все статусы без оценки сразу;
 * - empty-org/new-service — оценку посчитать не из чего.
 * Остальные собираются из каталога с типовыми формулировками.
 *
 * Метрики, рекомендации и причины в подробных отчётах — те, что отдают анализаторы на main:
 * Activity, Issues, CI/CD, Documentation и Code health, включая ошибки клиента SourceCraft
 * в reason. Так демо выглядит как настоящий отчёт.
 */

type NonMeasuredStatus = Exclude<CategoryStatus, "measured">;

interface CategoryDetails {
  summary?: string;
  reason?: string;
  metrics?: CategoryMetric[];
}

interface ReportDetails {
  analyzedMinutesAgo?: number;
  /** Точное время анализа, если он только что прошёл. */
  analyzedAt?: string;
  /** Идентификатор снимка: у запуска пользователя он свой. */
  analysisId?: string;
  categories?: Partial<Record<MockCategoryCode, CategoryDetails>>;
  recommendations?: Recommendation[];
  scoreLimitSummary?: string;
}

export function findMockReport(analysisId: string): RepositoryReport | null {
  const run = findRun(analysisId);
  const repository = run
    ? mockRepositories.find((item) => item.id === run.repositoryId)
    : findMockRepositoryByAnalysisId(analysisId);

  if (!repository || repository.categories === null) {
    return null;
  }
  // Как и настоящий backend, не выдаём, что закрытый репозиторий существует.
  if (repository.visibility === "private" && !mockSession.isSignedIn()) {
    return null;
  }

  if (run) {
    // Отчёт по запуску пользователя: свежие время и идентификатор снимка.
    if (Date.now() - run.createdAt < MOCK_ANALYSIS_DURATION_MS) {
      return null;
    }
    return buildMockReport(repository, {
      ...detailsFor(repository),
      analysisId: run.id,
      analyzedAt: finishedAt(run),
    });
  }

  if (repository.awaitingFirstAnalysis || repository.lastAnalysisFailed) {
    return null;
  }
  return buildMockReport(repository, detailsFor(repository));
}

export function buildMockReport(repository: MockRepository, details: ReportDetails = {}): RepositoryReport {
  const values = repository.categories;
  if (values === null) {
    throw new Error("Нельзя построить отчёт без категорий");
  }

  const scored = scoreMockCategories(values, repository.scoreLimit);
  const limited =
    repository.scoreLimit !== undefined &&
    scored.uncappedScore !== null &&
    scored.uncappedScore > repository.scoreLimit;

  const categories = scored.categories.map((category): ReportCategory => {
    const text = details.categories?.[category.code];
    return {
      code: category.code,
      label: category.label,
      status: category.status,
      score: category.score,
      weight: category.weight,
      effectiveWeight: category.effectiveWeight,
      points: category.points,
      summary: text?.summary ?? defaultSummary(category),
      reason: category.status === "measured" ? null : (text?.reason ?? defaultReason(category.code, category.status)),
      evidence: text?.metrics ?? [],
    };
  });

  return {
    repository: {
      id: repository.id,
      organizationSlug: repository.organizationSlug,
      repositorySlug: repository.repositorySlug,
      name: `${repository.organizationSlug}/${repository.repositorySlug}`,
      url: sourceCraftUrl(repository),
    },
    badgeAvailable: repository.visibility === "public",
    analysis: {
      id: details.analysisId ?? mockAnalysisId(repository),
      status: scored.status,
      analyzedAt: details.analyzedAt ?? minutesAgo(details.analyzedMinutesAgo ?? 190),
      commitSha: fakeCommitSha(repository.id),
      methodologyVersion: "v2",
      coverage: scored.coverage,
      isPreliminary: scored.isPreliminary,
      scoreLimit:
        limited && repository.scoreLimit !== undefined && scored.uncappedScore !== null
          ? {
              value: repository.scoreLimit,
              uncappedScore: scored.uncappedScore,
              code: "security-open-critical",
              summary:
                details.scoreLimitSummary ?? "Есть подтверждённая открытая критическая AppSec-уязвимость.",
            }
          : null,
    },
    score: scored.score,
    scoreDetails: { measuredWeight: scored.measuredWeight, applicableWeight: scored.applicableWeight },
    categories,
    recommendations: sortByPriority(details.recommendations ?? defaultRecommendations(scored.categories)),
  };
}

/* Подробные отчёты */

function detailsFor(repository: MockRepository): ReportDetails {
  const base = sourceCraftUrl(repository);
  const sha = fakeCommitSha(repository.id);

  switch (`${repository.organizationSlug}/${repository.repositorySlug}`) {
    case "gorod-dev/transit-api":
      return {
        analyzedMinutesAgo: 25,
        categories: {
          // Как backend/app/analyzers/security.py: у категории без данных — метрика доступности со ссылкой на обзор.
          security: {
            summary: "Результаты AppSec не получены.",
            reason: "appsec_not_available",
            metrics: [
              metric("appsec_data_availability", "unavailable", null, "SourceCraft вернул отсутствие результатов AppSec.", [
                securityOverview(base, "SourceCraft вернул отсутствие результатов AppSec."),
              ]),
            ],
          },
          // docs/scoring-methodology.md, §4.1: оценка — доля успешных автоматических прогонов, 23 из 40 ≈ 58.
          // Как backend/app/analyzers/cicd.py: история запусков в обеих метриках и ссылки на последние упавшие.
          cicd: {
            summary: "Успешно прошли 23 из 40 автоматических прогонов CI за полгода.",
            metrics: [
              metric("automated_ci_outcome_runs", 40, null, "Автоматических прогонов CI с итогом за полгода: 40.", [
                ciHistory(base),
              ]),
              metric("automated_ci_success_rate", 57.5, 57.5, "Успешно 23 из 40 автоматических прогонов CI.", [
                ciHistory(base),
                ...failedRuns(base, [4821, 4817, 4809]),
              ]),
            ],
          },
          // backend/app/analyzers/documentation.py: нет только CODEOWNERS, 100 − 15 = 85.
          documentation: {
            summary: "Оценка документации: 85/100. Проверены базовые файлы репозитория.",
            metrics: documentationMetrics(["has_codeowners"]),
          },
          // Метрики и веса v2 — docs/scoring-methodology.md, §3:
          // (0,4×96 + 0,25×87,5 + 0,25×100 + 0,2×67 + 0,15×100) ÷ 1,25 ≈ 91.
          activity: {
            summary:
              "Последние изменения 20 дней назад, за полгода коммиты были в 7 неделях, смержено 38 merge requests и вышло 2 релиза.",
            metrics: [
              metric("last_activity_days", 20, 96, "Последняя активность в репозитории — 20 дней назад.", []),
              metric("active_weeks_in_period", 7, 87.5, "За полгода коммиты были в 7 неделях.", [
                { source: "sourcecraft-activity", reference: "commit-history", summary: "За полгода коммиты были в 7 неделях.", url: base },
              ]),
              metric("merged_mr_in_period", 38, 100, "За полгода смержено 38 merge requests; в оценку идут не больше трёх.", [
                pull(base, 412, "«Кэш расписаний на границе суток» — смержен"),
              ]),
              metric("releases_in_period", 2, 67, "За полгода вышло 2 релиза, последний — v2.14.0.", [
                { source: "sourcecraft-releases", reference: "v2.14.0", summary: "последний релиз", url: `${base}/releases/v2.14.0` },
              ]),
              metric("contributor_count", 14, 100, "В списке участников 14 человек.", []),
            ],
          },
          // §2: 0,45×44 + 0,3×50 + 0,25×92 ≈ 58.
          issues: {
            summary: "7 из 23 открытых задач не двигались дольше 90 дней, а новые приходят быстрее, чем их решают.",
            metrics: [
              metric("stale_open_ratio", 0.3, 44, "7 из 23 открытых задач без движения дольше 90 дней.", [
                issue(base, 311, "142 дня без движения"),
                issue(base, 298, "131 день без движения"),
                issue(base, 276, "118 дней без движения"),
              ]),
              metric("backlog_trend", 0.63, 50, "За полгода решили 25 задач из 40 новых.", []),
              metric("median_days_to_close", 21, 92, "Обычно задачу решают за 21 день — это медиана по задачам, решённым за полгода.", []),
            ],
          },
          // backend/app/analyzers/code_health.py: 100 − (5 × 7 FIXME + 40 TODO) ÷ 250 файлов × 100 = 70.
          code_health: {
            summary: "Оценка чистоты кода: 70.0/100. Обнаружено TODO: 40, FIXME: 7.",
            metrics: codeHealthMetrics(250, 40, 7, 21),
          },
        },
        recommendations: [
          {
            code: "cicd-investigate-failed-runs",
            priority: "p1",
            problem: "За полгода упали 17 из 40 автоматических прогонов CI.",
            action: "Разобрать повторяющиеся падения CI и устранить их причины",
            rationale: "Нестабильная автоматическая проверка замедляет выпуск изменений и снижает доверие к результатам сборки.",
            expectedEffect: "CI/CD поднимется примерно с 58 до 80.",
            expectedScoreDelta: 5.9,
            evidence: failedRuns(base, [4821, 4817, 4809]),
          },
          {
            code: "issues-triage-stale",
            priority: "p1",
            problem: "7 из 23 открытых задач не двигались дольше 90 дней.",
            action: "Разобрать зависшие задачи: неактуальные закрыть, остальным назначить ответственного и срок",
            rationale: "Задачи без движения показывают, что обращения пользователей остаются без ответа, и снижают доверие к проекту.",
            expectedEffect: "Работа с issues поднимется примерно с 58 до 75.",
            expectedScoreDelta: 3.4,
            evidence: [
              issue(base, 311, "«Нет расписания для маршрута 47» — 142 дня"),
              issue(base, 298, "«Неверное время прибытия по выходным» — 131 день"),
              issue(base, 276, "«Выгрузка в GTFS» — 118 дней"),
              issue(base, 305, "«Падает импорт с пустой строкой» — 104 дня"),
              issue(base, 289, "«Документация по /v2/stops» — 96 дней"),
            ],
          },
          {
            code: "security-enable-appsec",
            priority: "p2",
            problem: "За последние 90 дней в репозитории нет результатов SAST, SCA и secret scanning.",
            action: "Включить сканирование AppSec",
            rationale: "На безопасность приходится четверть оценки. Пока скана нет, она неизвестна, а Score остаётся предварительным.",
            expectedEffect: "Оценка станет полной. Как изменится Score, зависит от результатов скана.",
            expectedScoreDelta: null,
            evidence: [{ source: "sourcecraft-appsec", reference: "AppSec", summary: "нет ни одного завершённого скана", url: null }],
          },
          // Прирост — по итоговому Score, как требует контракт: 15 баллов документации × 20 ÷ 75.
          documentationRecommendation("has_codeowners", sha, 4),
          // 14 баллов Code health без FIXME × 5 ÷ 75 и 16 баллов без TODO × 5 ÷ 75.
          fixmeRecommendation(7, 0.9, [
            marker("src/sync/importer.ts", 118, "FIXME"),
            marker("src/sync/importer.ts", 243, "FIXME"),
            marker("src/routes/stops.ts", 57, "FIXME"),
            marker("src/routes/stops.ts", 57, "TODO"),
            marker("src/gtfs/export.ts", 12, "FIXME"),
          ]),
          todoRecommendation(40, 1.1, [
            marker("src/routes/stops.ts", 57, "TODO"),
            marker("src/cache/schedule.ts", 31, "TODO"),
            marker("src/cache/schedule.ts", 88, "TODO"),
          ]),
        ],
      };

    case "lastochka/android-sdk":
      return {
        analyzedMinutesAgo: 70,
        scoreLimitSummary: "Открыты две критические уязвимости в зависимостях SDK.",
        categories: {
          security: {
            summary: "AppSec нашёл 2 критические уязвимости в зависимостях.",
            metrics: [
              metric("critical_findings", 2, 22, "jackson-databind 2.9.10 и commons-text 1.9.", [
                finding(base, "SCA-1182", "jackson-databind 2.9.10: десериализация недоверенных данных"),
                finding(base, "SCA-1187", "commons-text 1.9: выполнение кода через интерполяцию строк"),
              ]),
            ],
          },
          cicd: { summary: "Успешно прошли 38 из 40 автоматических прогонов CI за полгода." },
          // В README нет ни раздела про запуск и тесты, ни команды для них: 100 − 15 = 85.
          documentation: {
            summary: "Оценка документации: 85/100. Проверены базовые файлы репозитория.",
            metrics: documentationMetrics(["has_shortcuts"]),
          },
          activity: { summary: "Релизы выходят раз в две-три недели, последний — 4.8.1." },
          issues: { summary: "Обычно задачу решают за 9 дней, 3 из 41 открытой не двигались дольше 90 дней." },
          // 100 − (5 × 5 FIXME + 32 TODO) ÷ 300 файлов × 100 = 81.
          code_health: {
            summary: "Оценка чистоты кода: 81.0/100. Обнаружено TODO: 32, FIXME: 5.",
            metrics: codeHealthMetrics(300, 32, 5, 24),
          },
        },
        recommendations: [
          {
            code: "security-critical-dependencies",
            priority: "p0",
            problem: "AppSec нашёл 2 критические уязвимости в зависимостях SDK.",
            action: "Обновить jackson-databind и commons-text до безопасных версий",
            rationale:
              "Обе уязвимости публично известны и легко эксплуатируются. SDK встраивается в чужие приложения, поэтому риск переходит и к ним.",
            expectedEffect: "Снимет ограничение Score, «Безопасность» поднимется примерно до 80.",
            expectedScoreDelta: 23.8,
            evidence: [
              finding(base, "SCA-1182", "критичность critical"),
              finding(base, "SCA-1187", "критичность critical"),
            ],
          },
          // Пока действует ограничение Score, прирост от остальных рекомендаций оценить нельзя.
          {
            ...documentationRecommendation("has_shortcuts", sha, null),
            expectedEffect: "Документация поднимется до 100, но Score не изменится, пока действует ограничение.",
          },
          {
            ...fixmeRecommendation(5, null, [
              marker("sdk/src/main/kotlin/pay/Checkout.kt", 204, "FIXME"),
              marker("sdk/src/main/kotlin/pay/Checkout.kt", 311, "FIXME"),
              marker("sdk/src/main/kotlin/pay/Tokenizer.kt", 77, "FIXME"),
              marker("sdk/src/main/kotlin/pay/Tokenizer.kt", 132, "FIXME"),
              marker("sdk/src/main/kotlin/pay/Receipt.kt", 18, "FIXME"),
            ]),
            expectedEffect: "Состояние кода поднимется, но Score не изменится, пока действует ограничение.",
          },
          todoRecommendation(32, null, [marker("sdk/src/main/kotlin/pay/Receipt.kt", 45, "TODO")]),
        ],
      };

    case "edu-kit/olympiad-judge":
      return {
        analyzedMinutesAgo: 300,
        categories: {
          security: { summary: "Открытых уязвимостей высокой критичности нет, одна средняя — в зависимости сборки." },
          // Ошибка клиента SourceCraft попадает в reason как есть — отчёт пересказывает её по-русски.
          cicd: { summary: "Не удалось получить данные из SourceCraft.", reason: "SourceCraft request timed out" },
          // Нет CONTRIBUTING: 100 − 20 = 80.
          documentation: {
            summary: "Оценка документации: 80/100. Проверены базовые файлы репозитория.",
            metrics: documentationMetrics(["has_contributing"]),
          },
          activity: { summary: "Истории пока мало: репозиторий перенесли в SourceCraft три недели назад." },
          // backend/app/analyzers/issues.py: задач нет — категория неприменима, reason — готовая фраза.
          issues: {
            summary: "В репозитории нет задач: работу с обращениями оценивать не на чем.",
            reason: "Трекер задач не используется.",
          },
          // 100 − (5 × 3 FIXME + 19 TODO) ÷ 100 файлов × 100 = 66.
          code_health: {
            summary: "Оценка чистоты кода: 66.0/100. Обнаружено TODO: 19, FIXME: 3.",
            metrics: codeHealthMetrics(100, 19, 3, 12),
          },
        },
        // Измерены безопасность, документация и Code health — вместе 50% веса.
        recommendations: [
          // 20 баллов документации × 20 ÷ 50.
          documentationRecommendation("has_contributing", sha, 8),
          // 15 баллов Code health без FIXME × 5 ÷ 50 и 19 баллов без TODO × 5 ÷ 50.
          fixmeRecommendation(3, 1.5, [
            marker("checker/limits.cpp", 88, "FIXME"),
            marker("checker/limits.cpp", 142, "FIXME"),
            marker("checker/sandbox.cpp", 23, "FIXME"),
          ]),
          todoRecommendation(19, 1.9, [
            marker("checker/limits.cpp", 61, "TODO"),
            marker("checker/interactor.cpp", 9, "TODO"),
            marker("checker/interactor.cpp", 214, "TODO"),
            marker("web/submit.py", 40, "TODO"),
          ]),
        ],
      };

    case "empty-org/new-service":
      return {
        analyzedMinutesAgo: 12,
        categories: {
          security: { summary: "Результаты AppSec не получены.", reason: "appsec_not_available" },
          cicd: { summary: "Данные CI не получены.", reason: "analyzer_execution_failed" },
          // Так backend/app/analysis/providers.py описывает неудачный git-клон: reason продолжает summary.
          documentation: {
            summary: "Не удалось получить содержимое репозитория.",
            reason: "Не удалось получить содержимое репозитория: git-команда завершилась ошибкой.",
          },
          activity: { summary: "Репозиторий создан пять дней назад: merge requests и релизов ещё не было." },
          // Issues склеивает ошибки открытого и закрытого списков через «; ».
          issues: {
            summary: "Не удалось получить задачи репозитория.",
            reason: "open: SourceCraft denied access with HTTP 403; closed: SourceCraft denied access with HTTP 403",
          },
          code_health: {
            summary: "Не удалось получить содержимое репозитория.",
            reason: "Не удалось получить содержимое репозитория: git-команда завершилась ошибкой.",
          },
        },
        recommendations: [],
      };

    default:
      return {};
  }
}

/* Типовые формулировки для остальных репозиториев */

const bandSummaries: Record<MockCategoryCode, Record<ScoreBand, string>> = {
  security: {
    high: "Открытых уязвимостей высокой критичности нет, последние находки AppSec исправлены.",
    mid: "В зависимостях есть открытые уязвимости средней критичности.",
    low: "Открыты уязвимости высокой критичности, часть из них висит больше месяца.",
  },
  cicd: {
    high: "Почти все автоматические прогоны CI проходят успешно.",
    mid: "Заметная часть автоматических прогонов CI падает.",
    low: "Автоматические прогоны CI падают почти так же часто, как проходят.",
  },
  documentation: {
    high: "README объясняет запуск и тесты, есть лицензия и CONTRIBUTING.",
    mid: "README есть, но в нём не хватает инструкции по запуску или тестам.",
    low: "Нет README с инструкциями или не указана лицензия.",
  },
  activity: {
    high: "Проект меняют постоянно: merge requests принимают, релизы выходят.",
    mid: "Изменения выходят, но с перерывами в несколько недель.",
    low: "Больше трёх месяцев без изменений.",
  },
  issues: {
    high: "Задачи разбирают быстро, зависших почти нет.",
    mid: "Несколько задач не двигаются дольше 90 дней.",
    low: "Много открытых задач без движения, самые старые висят больше полугода.",
  },
  code_health: {
    high: "TODO и FIXME почти нет.",
    mid: "TODO и FIXME встречаются, но на файл их немного.",
    low: "TODO и FIXME разбросаны по всему коду, среди них много FIXME.",
  },
};

const statusSummaries: Record<NonMeasuredStatus, string> = {
  unavailable: "Данных нет.",
  insufficient_sample: "Данных пока мало.",
  not_applicable: "Категория не относится к этому репозиторию.",
  error: "Данные не получены.",
};

function defaultSummary(category: MockScoredCategory): string {
  if (category.status === "measured" && category.score !== null) {
    return bandSummaries[category.code][getScoreBand(category.score)];
  }
  return statusSummaries[category.status as NonMeasuredStatus];
}

function defaultReason(code: MockCategoryCode, status: NonMeasuredStatus): string | null {
  if (status === "not_applicable" || status === "insufficient_sample") {
    return null;
  }
  if (code === "security") {
    return "appsec_not_available";
  }
  return status === "error" ? "analyzer_execution_failed" : "analyzer_not_configured";
}

interface RecommendationTemplate {
  priority: RecommendationPriority;
  action: string;
  problem: string;
  rationale: string;
}

const lowScoreTemplates: Record<MockCategoryCode, RecommendationTemplate> = {
  security: {
    priority: "p0",
    action: "Закрыть уязвимости высокой критичности",
    problem: "AppSec нашёл открытые уязвимости высокой критичности.",
    rationale: "Это самый прямой риск для тех, кто использует проект.",
  },
  // Как cicd-investigate-failed-runs в backend/app/analyzers/cicd.py: P1 при успешности ниже 80 %.
  cicd: {
    priority: "p1",
    action: "Разобрать повторяющиеся падения CI и устранить их причины",
    problem: "Значительная часть автоматических прогонов CI за полгода завершилась с ошибкой.",
    rationale: "Когда CI всё время красный, настоящие ошибки в коде легко пропустить.",
  },
  issues: {
    priority: "p1",
    action: "Разобрать зависшие задачи",
    problem: "Много задач не двигаются дольше 90 дней.",
    rationale: "Пользователи и контрибьюторы уходят, если их обращения остаются без ответа.",
  },
  documentation: {
    priority: "p2",
    action: "Дописать README: запуск, тесты, лицензия",
    problem: "В документации не хватает базовых разделов.",
    rationale: "Без инструкции новому человеку трудно запустить проект и внести изменения.",
  },
  // docs/scoring-methodology.md, §3.3: activity-stale-repository — P1.
  activity: {
    priority: "p1",
    action: "Вернуть регулярные изменения: даже небольшой релиз или merge показывает, что проект жив",
    problem: "Больше трёх месяцев в репозитории ничего не менялось.",
    rationale: "Долгое отсутствие обновлений говорит, что проектом перестали пользоваться как рабочей кодовой базой.",
  },
  // Как code_health_resolve_fixme в backend/app/analyzers/code_health.py: P1 от двух FIXME.
  code_health: {
    priority: "p1",
    action: "Устранить FIXME или перенести их в трекер задач",
    problem: "В коде остались неразрешённые пометки FIXME.",
    rationale: "FIXME отмечает заведомо неработающий или опасный код.",
  },
};

function defaultRecommendations(categories: MockScoredCategory[]): Recommendation[] {
  const recommendations: Recommendation[] = [];

  for (const category of categories) {
    if (category.status === "measured" && category.score !== null && getScoreBand(category.score) === "low") {
      const target = 80;
      const delta = ((target - category.score) * (category.effectiveWeight ?? 0)) / 100;
      recommendations.push({
        code: `${category.code}-low`,
        ...lowScoreTemplates[category.code],
        expectedEffect: `${category.label}: примерно с ${Math.round(category.score)} до ${target}.`,
        expectedScoreDelta: Math.round(delta * 10) / 10,
        evidence: [],
      });
    }
    if (category.code === "security" && category.status === "unavailable") {
      recommendations.push({
        code: "security-enable-appsec",
        priority: "p2",
        problem: "В репозитории нет результатов SAST, SCA и secret scanning.",
        action: "Включить сканирование AppSec",
        rationale: "На безопасность приходится четверть оценки. Пока скана нет, Score остаётся предварительным.",
        expectedEffect: "Оценка станет полной.",
        expectedScoreDelta: null,
        evidence: [],
      });
    }
  }
  return recommendations;
}

/* Вспомогательные функции */

const priorityOrder: Record<RecommendationPriority, number> = { p0: 0, p1: 1, p2: 2, p3: 3 };

function sortByPriority(recommendations: Recommendation[]): Recommendation[] {
  return [...recommendations].sort((a, b) => priorityOrder[a.priority] - priorityOrder[b.priority]);
}

function metric(
  code: string,
  value: number | string | null,
  normalizedScore: number | null,
  summary: string,
  evidence: Evidence[],
): CategoryMetric {
  return { code, value, normalizedScore, summary, evidence };
}

/*
 * Документация — как backend/app/analyzers/documentation.py: пять признаков «есть/нет»
 * (value 1 или 0, оценка 100 или 0), а оценка категории — 100 минус штрафы за то, чего нет:
 * README 35, CONTRIBUTING 20, лицензия, CODEOWNERS и инструкция запуска — по 15.
 * Без README инструкцию запуска не проверяют.
 */
type DocumentationCheck = "has_readme" | "has_contributing" | "has_license" | "has_codeowners" | "has_shortcuts";

interface DocumentationCheckText {
  label: string;
  priority: RecommendationPriority;
  problem: string;
  action: string;
}

const documentationChecks: Record<DocumentationCheck, DocumentationCheckText> = {
  has_readme: {
    label: "README.md",
    priority: "p1",
    problem: "В репозитории нет README.md.",
    action: "Добавить README.md с описанием архитектуры и назначения проекта",
  },
  has_contributing: {
    label: "CONTRIBUTING.md",
    priority: "p2",
    problem: "В репозитории нет CONTRIBUTING.md.",
    action: "Создать CONTRIBUTING.md с правилами ведения веток и код-ревью",
  },
  has_license: {
    label: "LICENSE",
    priority: "p2",
    problem: "В репозитории нет файла лицензии.",
    action: "Добавить файл LICENSE или COPYING",
  },
  has_codeowners: {
    label: "CODEOWNERS",
    priority: "p3",
    problem: "В репозитории нет файла CODEOWNERS.",
    action: "Настроить CODEOWNERS, чтобы ревьюеры назначались автоматически",
  },
  has_shortcuts: {
    label: "Инструкции запуска",
    priority: "p2",
    problem: "В README нет команд для запуска проекта и тестов.",
    action: "Добавить в README команды быстрого запуска проекта и тестов",
  },
};

function documentationMetrics(missing: DocumentationCheck[]): CategoryMetric[] {
  const hasReadme = !missing.includes("has_readme");
  return (Object.keys(documentationChecks) as DocumentationCheck[])
    .filter((code) => code !== "has_shortcuts" || hasReadme)
    .map((code) => {
      const present = !missing.includes(code);
      const { label } = documentationChecks[code];
      return metric(code, present ? 1 : 0, present ? 100 : 0, `Наличие файла/информации: ${label}`, []);
    });
}

function documentationRecommendation(
  check: DocumentationCheck,
  commitSha: string,
  expectedScoreDelta: number | null,
): Recommendation {
  const { priority, problem, action } = documentationChecks[check];
  return {
    code: `doc_missing_${check}`,
    priority,
    problem,
    action,
    rationale: "Понятная документация и прозрачные правила упрощают вход в проект и ускоряют выпуск изменений.",
    expectedEffect: null,
    expectedScoreDelta,
    evidence: [
      {
        source: "repository_structure",
        reference: commitSha,
        summary: "Проверка файлов регламентов и инструкций в корне репозитория.",
        url: null,
      },
    ],
  };
}

/*
 * Code health — как backend/app/analyzers/code_health.py: справочные числа без оценки,
 * а оценка категории — 100 минус (5 × FIXME + TODO) на файл кода × 100. Возраст пометок
 * backend не считает: клон без истории, value — null.
 */
function codeHealthMetrics(files: number, todos: number, fixmes: number, debtFiles: number): CategoryMetric[] {
  return [
    metric("total_analyzed_files", files, null, "Всего проанализировано файлов кода", []),
    metric("todo_count", todos, null, "Количество меток TODO в комментариях кода", []),
    metric("fixme_count", fixmes, null, "Количество критических меток FIXME в комментариях кода", []),
    metric(
      "code_health.debt_file_ratio",
      debtFiles / files,
      null,
      "Доля файлов с техническим долгом (files_with_debt / total_files)",
      [],
    ),
    metric("code_health.marker_age", null, null, "Возраст TODO/FIXME недоступен: клон shallow, истории для blame нет.", []),
  ];
}

function fixmeRecommendation(fixmes: number, expectedScoreDelta: number | null, evidence: Evidence[]): Recommendation {
  return {
    code: "code_health_resolve_fixme",
    priority: fixmes >= 2 ? "p1" : "p2",
    problem: `В коде остались неразрешённые пометки FIXME (${fixmes} шт.).`,
    action: "Устранить FIXME или перенести их в трекер задач",
    rationale: "FIXME отмечает заведомо неработающий или опасный код.",
    expectedEffect: null,
    expectedScoreDelta,
    evidence,
  };
}

function todoRecommendation(todos: number, expectedScoreDelta: number | null, evidence: Evidence[]): Recommendation {
  return {
    code: "code_health_clear_todos",
    priority: "p3",
    problem: `В коде скопилось много пометок TODO (${todos} шт.).`,
    action: "Пересмотреть TODO и убрать неактуальные",
    rationale: "Когда пометок слишком много, важные теряются среди остальных.",
    expectedEffect: null,
    expectedScoreDelta,
    evidence,
  };
}

/** Пометка в коде — такой факт Code health кладёт в рекомендацию: путь и номер строки. */
function marker(path: string, line: number, kind: "TODO" | "FIXME"): Evidence {
  return { source: "git_repository", reference: `${path}:${line}`, summary: `${kind} на строке ${line} в файле ${path}.`, url: null };
}

export function sourceCraftUrl(repository: MockRepository): string {
  return `https://sourcecraft.dev/${repository.organizationSlug}/${repository.repositorySlug}`;
}

/* Ссылки на страницы SourceCraft — как их строят backend/app/analyzers/cicd.py и security.py. */
function ciHistory(base: string): Evidence {
  return {
    source: "sourcecraft-cicd",
    reference: "ci-runs",
    summary:
      "История запусков CI/CD в SourceCraft. Страница показывает текущее состояние, а отчёт рассчитан за указанный период.",
    url: `${base}/cicd/runs`,
  };
}

function failedRuns(base: string, runs: number[]): Evidence[] {
  return runs.map((run) => ({
    source: "sourcecraft-cicd",
    reference: `ci-run-${run}`,
    summary: "Неуспешный запуск CI/CD: ошибка или тайм-аут.",
    url: `${base}/cicd/runs/${run}`,
  }));
}

function securityOverview(base: string, summary: string): Evidence {
  return {
    source: "sourcecraft-appsec",
    reference: "appsec-defects",
    summary: `${summary} Ссылка открывает текущий обзор SourceCraft, который может отличаться от снимка отчёта.`,
    url: `${base}/security/overview`,
  };
}

function issue(base: string, id: number, summary: string): Evidence {
  return { source: "sourcecraft-issues", reference: `#${id}`, summary, url: `${base}/issues/${id}` };
}

function pull(base: string, id: number, summary: string): Evidence {
  return { source: "sourcecraft-pulls", reference: `MR !${id}`, summary, url: `${base}/pr/${id}` };
}

function finding(base: string, id: string, summary: string): Evidence {
  return { source: "sourcecraft-appsec", reference: id, summary, url: `${base}/security/findings/${id}` };
}

function fakeCommitSha(seed: string): string {
  let hash = 2166136261;
  let sha = "";
  while (sha.length < 40) {
    for (const char of `${seed}:${sha.length}`) {
      hash ^= char.charCodeAt(0);
      hash = Math.imul(hash, 16777619);
    }
    sha += (hash >>> 0).toString(16).padStart(8, "0");
  }
  return sha.slice(0, 40);
}
