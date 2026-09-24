/**
 * Backend присылает машинный код причины, по которой у категории нет оценки
 * (docs/api-contract.md). Здесь коды превращаются в понятный текст. Он дополняет
 * summary категории, а не повторяет его: summary пишет backend, здесь — что это значит.
 * Незнакомый код не прячем: показываем как есть, чтобы его было видно в отчёте.
 */

const reasonTexts: Record<string, string> = {
  // Security — docs/security-analyzer.md.
  appsec_not_available: "Сканирование не запускалось или его результаты недоступны — это не значит, что уязвимостей нет.",
  appsec_unavailable: "Сканирование не запускалось или его результаты недоступны — это не значит, что уязвимостей нет.",
  appsec_source_error: "SourceCraft не ответил на запрос результатов сканирования. Повторим при следующем анализе.",
  security_scoring_not_configured: "Результаты сканирования есть, но правила оценки безопасности ещё не утверждены.",
  // CI/CD — docs/scoring-methodology.md, раздел 4.1.
  cicd_runs_unavailable: "SourceCraft не отдал историю прогонов CI — повторим при следующем анализе.",
  cicd_runs_truncated: "История прогонов прочитана не полностью, а по её части оценку не ставим.",
  cicd_no_runs: "Прогонов CI ещё не было. Это не значит, что CI не нужен.",
  cicd_no_automated_runs_in_period: "За полгода не было автоматических прогонов: ручные запуски в оценку не идут.",
  cicd_too_few_outcome_runs: "Для оценки нужно хотя бы 5 завершённых автоматических прогонов.",
  // Общие причины runner и ядра анализа.
  analyzer_not_configured: "Проверку этой части ещё не подключили — с ней самой всё может быть в порядке.",
  analyzer_execution_failed: "При проверке этой части произошёл сбой. Повторим при следующем анализе.",
  analyzer_category_mismatch: "Проверка вернула данные не той части проекта, поэтому результат не засчитан.",
  analysis_execution_failed: "Анализ завершился с ошибкой.",
  repository_mismatch: "Данные пришли по другому репозиторию.",
  empty_repository: "В репозитории ещё нет файлов и истории — оценивать нечего.",
};

export function describeReason(code: string): string {
  return reasonTexts[code] ?? `Причина: ${code}`;
}

export function isKnownReason(code: string): boolean {
  return code in reasonTexts;
}
