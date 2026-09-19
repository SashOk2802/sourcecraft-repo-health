/**
 * Backend присылает машинный код причины, по которой у категории нет оценки
 * (docs/api-contract.md). Здесь коды превращаются в понятный текст.
 * Незнакомый код не прячем: показываем как есть, чтобы его было видно в отчёте.
 */

const reasonTexts: Record<string, string> = {
  appsec_not_available: "AppSec не вернул результатов: сканирование не запускалось или недоступно.",
  analyzer_not_configured: "Анализатор этой категории ещё не подключён.",
  analyzer_execution_failed: "Анализатор завершился с ошибкой. Повторим при следующем анализе.",
  analyzer_category_mismatch: "Анализатор вернул данные другой категории — результат не засчитан.",
  analysis_execution_failed: "Анализ завершился с ошибкой.",
  repository_mismatch: "Данные пришли по другому репозиторию.",
};

export function describeReason(code: string): string {
  return reasonTexts[code] ?? `Причина: ${code}`;
}

export function isKnownReason(code: string): boolean {
  return code in reasonTexts;
}
