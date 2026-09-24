import { getScoreBand, type ScoreBand } from "../../lib/scoreBands";
import type { CategoryStatus, Evidence, RecommendationPriority } from "../common";
import type { CategoryMetric, Recommendation, ReportCategory, RepositoryReport } from "../report";
import {
  findMockRepositoryByAnalysisId,
  mockAnalysisId,
  type MockCategoryCode,
  type MockRepository,
} from "./catalog";
import { scoreMockCategories, type MockScoredCategory } from "./scoring";
import { minutesAgo } from "./time";

/*
 * Mock-отчёты в формате docs/api-contract.md. Подробно расписаны четыре случая:
 * - gorod-dev/transit-api — нет данных AppSec, оценка предварительная;
 * - lastochka/android-sdk — критическая уязвимость ограничивает Score;
 * - edu-kit/olympiad-judge — все статусы без оценки сразу;
 * - empty-org/new-service — оценку посчитать не из чего.
 * Остальные собираются из каталога с типовыми формулировками.
 */

type NonMeasuredStatus = Exclude<CategoryStatus, "measured">;

interface CategoryDetails {
  summary?: string;
  reason?: string;
  metrics?: CategoryMetric[];
}

interface ReportDetails {
  analyzedMinutesAgo?: number;
  categories?: Partial<Record<MockCategoryCode, CategoryDetails>>;
  recommendations?: Recommendation[];
  scoreLimitSummary?: string;
}

export function findMockReport(analysisId: string): RepositoryReport | null {
  const repository = findMockRepositoryByAnalysisId(analysisId);
  if (!repository || repository.categories === null) {
    return null;
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
    analysis: {
      id: mockAnalysisId(repository),
      status: scored.status,
      analyzedAt: minutesAgo(details.analyzedMinutesAgo ?? 190),
      commitSha: fakeCommitSha(repository.id),
      methodologyVersion: "v1",
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

  switch (`${repository.organizationSlug}/${repository.repositorySlug}`) {
    case "gorod-dev/transit-api":
      return {
        analyzedMinutesAgo: 25,
        categories: {
          security: {
            summary: "Результаты AppSec не получены.",
            reason: "appsec_not_available",
          },
          // docs/scoring-methodology.md, §4.1: оценка — доля успешных автоматических прогонов, 23 из 40 ≈ 58.
          // Детали job backend в отчёт не выводит, поэтому и демо ссылается только на историю CI.
          cicd: {
            summary: "Успешно прошли 23 из 40 автоматических прогонов CI за полгода.",
            metrics: [
              metric("automated_ci_outcome_runs", 40, null, "Автоматических прогонов CI с итогом за полгода: 40.", [
                ciHistory(base, "push, merge request и расписание; ручные запуски не считаются"),
              ]),
              metric("automated_ci_success_rate", 57.5, 57.5, "Успешно 23 из 40 автоматических прогонов CI.", []),
            ],
          },
          documentation: {
            summary: "README с запуском и тестами, CONTRIBUTING и лицензия MIT. Нет CODEOWNERS.",
            metrics: [
              metric("readme_sections", "запуск, тесты", 100, "README объясняет, как запустить и проверить проект.", [
                file(base, "README.md", "разделы «Запуск» и «Тесты»"),
              ]),
              metric("codeowners", "нет", 0, "Файла CODEOWNERS нет: непонятно, кого звать на ревью.", []),
            ],
          },
          // Метрики и веса — как в docs/scoring-methodology.md, §3: 0,4×94 + 0,25×100 + 0,2×67 + 0,15×100 ≈ 91.
          activity: {
            summary: "Последние изменения 24 дня назад, за полгода смержено 38 merge requests и вышло 2 релиза.",
            metrics: [
              metric("last_activity_days", 24, 94, "Последняя активность в репозитории — 24 дня назад.", []),
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
          code_health: {
            summary: "47 TODO и FIXME в 31 файле, 12 из них старше полугода.",
            metrics: [
              metric("todo_fixme_count", 47, 70, "Больше всего пометок в модуле синхронизации.", [
                file(base, "src/sync/importer.ts", "FIXME: дубли остановок при импорте"),
                file(base, "src/routes/stops.ts", "TODO: кэшировать ответ"),
              ]),
            ],
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
            evidence: [ciHistory(base, "17 неуспешных прогонов за полгода")],
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
          {
            code: "docs-codeowners",
            priority: "p3",
            problem: "В репозитории нет файла CODEOWNERS.",
            action: "Добавить файл CODEOWNERS",
            rationale: "Станет понятно, кого звать на ревью в каждую часть кода, и merge requests не будут ждать случайного ревьюера.",
            expectedEffect: "Документация поднимется примерно с 88 до 94.",
            expectedScoreDelta: 1.6,
            evidence: [{ source: "sourcecraft-repository", reference: "CODEOWNERS", summary: "файла нет ни в корне, ни в .sourcecraft/", url: null }],
          },
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
          documentation: {
            summary: "README с примерами подключения SDK, лицензия Apache 2.0 и CONTRIBUTING. Не описано, как запускать тесты.",
          },
          activity: { summary: "Релизы выходят раз в две-три недели, последний — 4.8.1." },
          issues: { summary: "Обычно задачу решают за 9 дней, 3 из 41 открытой не двигались дольше 90 дней." },
          code_health: { summary: "22 TODO и FIXME, 4 из них старше года." },
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
          {
            code: "docs-tests",
            priority: "p3",
            problem: "В README нет раздела про тесты.",
            action: "Описать в README, как запускать тесты",
            rationale: "Внешним контрибьюторам приходится разбираться по конфигурации Gradle.",
            expectedEffect: "Документация поднимется примерно с 84 до 90, но Score не изменится, пока действует ограничение.",
            expectedScoreDelta: null,
            evidence: [file(base, "README.md", "нет раздела про тесты")],
          },
        ],
      };

    case "edu-kit/olympiad-judge":
      return {
        analyzedMinutesAgo: 300,
        categories: {
          security: { summary: "Открытых уязвимостей высокой критичности нет, одна средняя — в зависимости сборки." },
          cicd: { summary: "Данные CI не получены.", reason: "analyzer_execution_failed" },
          documentation: { summary: "README описывает сборку и запуск, но не объясняет, как прогнать тесты." },
          activity: { summary: "Истории пока мало: репозиторий перенесли в SourceCraft три недели назад." },
          issues: { summary: "Issues отключены: команда ведёт задачи вне SourceCraft." },
          code_health: { summary: "31 TODO и FIXME, почти все — в модуле checker/." },
        },
        recommendations: [
          {
            code: "docs-tests",
            priority: "p2",
            problem: "В README нет раздела про тесты.",
            action: "Описать в README, как прогнать тесты",
            rationale: "Участники олимпиад и новые разработчики не смогут проверить свои изменения перед отправкой.",
            expectedEffect: "Документация поднимется примерно до 88.",
            expectedScoreDelta: 2.4,
            evidence: [file(base, "README.md", "есть «Сборка» и «Запуск», нет «Тесты»")],
          },
          {
            code: "code-todo-checker",
            priority: "p3",
            problem: "27 из 31 TODO и FIXME находятся в checker/.",
            action: "Разобрать TODO в модуле checker/",
            rationale: "Это ядро проверки решений: недоделки здесь напрямую влияют на честность результатов.",
            expectedEffect: "Состояние кода поднимется примерно до 75.",
            expectedScoreDelta: 0.6,
            evidence: [file(base, "checker/limits.cpp", "TODO: учитывать память дочерних процессов")],
          },
        ],
      };

    case "empty-org/new-service":
      return {
        analyzedMinutesAgo: 12,
        categories: {
          security: { summary: "Результаты AppSec не получены.", reason: "appsec_not_available" },
          cicd: { summary: "Данные CI не получены.", reason: "analyzer_execution_failed" },
          documentation: { summary: "Дерево файлов недоступно: ветка по умолчанию пустая.", reason: "analyzer_execution_failed" },
          activity: { summary: "Репозиторий создан пять дней назад: merge requests и релизов ещё не было." },
          issues: { summary: "Нет доступа к issues репозитория.", reason: "analyzer_not_configured" },
          code_health: { summary: "Дерево файлов недоступно: ветка по умолчанию пустая.", reason: "analyzer_execution_failed" },
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
    mid: "Встречаются старые TODO и FIXME, но их немного.",
    low: "TODO и FIXME разбросаны по коду, многие старше года.",
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
  code_health: {
    priority: "p3",
    action: "Разобрать старые TODO и FIXME",
    problem: "В коде много давних TODO и FIXME.",
    rationale: "Старые пометки прячут настоящий технический долг.",
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

export function sourceCraftUrl(repository: MockRepository): string {
  return `https://sourcecraft.dev/${repository.organizationSlug}/${repository.repositorySlug}`;
}

function ciHistory(base: string, summary: string): Evidence {
  return { source: "sourcecraft-cicd", reference: "история CI", summary, url: `${base}/ci` };
}

function issue(base: string, id: number, summary: string): Evidence {
  return { source: "sourcecraft-issues", reference: `#${id}`, summary, url: `${base}/issues/${id}` };
}

function pull(base: string, id: number, summary: string): Evidence {
  return { source: "sourcecraft-pulls", reference: `MR !${id}`, summary, url: `${base}/pr/${id}` };
}

function file(base: string, path: string, summary: string): Evidence {
  return { source: "sourcecraft-repository", reference: path, summary, url: `${base}/browse/${path}` };
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
