/**
 * Причина, по которой у категории нет оценки. По контракту (docs/api-contract.md) backend
 * присылает машинный код — здесь он превращается в понятный текст. Текст дополняет summary
 * категории, а не повторяет его: summary пишет backend, здесь — что это значит.
 * Незнакомый код не прячем: показываем как есть, чтобы его было видно в отчёте.
 *
 * Часть анализаторов на main кладёт в reason не код, а готовую фразу: русскую
 * («Трекер задач не используется.») или текст ошибки клиента SourceCraft по-английски
 * («SourceCraft denied access with HTTP 403»). Русскую фразу показываем как есть,
 * ошибки клиента (backend/app/integrations/sourcecraft.py) — своими словами.
 */

const APPSEC_UNAVAILABLE = "Сканирование не запускалось или его результаты недоступны — это не значит, что уязвимостей нет.";
const RETRY = "Повторим при следующем анализе.";

const reasonTexts: Record<string, string> = {
  // Security — docs/security-analyzer.md.
  appsec_not_available: APPSEC_UNAVAILABLE,
  appsec_unavailable: APPSEC_UNAVAILABLE,
  sourcecraft_appsec_unavailable: APPSEC_UNAVAILABLE,
  appsec_source_error: `SourceCraft не ответил на запрос результатов сканирования. ${RETRY}`,
  security_scoring_not_configured: "Результаты сканирования есть, но правила оценки безопасности ещё не утверждены.",
  // docs/scoring-methodology.md, §4.2: оценку ставим только по полным результатам всех трёх сканеров.
  appsec_coverage_not_confirmed:
    "Сканеры вернули не полные результаты, а по части находок оценку безопасности не ставим — это не значит, что уязвимостей нет.",
  // CI/CD — docs/scoring-methodology.md, раздел 4.1.
  cicd_runs_unavailable: "SourceCraft не отдал историю прогонов CI — повторим при следующем анализе.",
  cicd_runs_truncated: "История прогонов прочитана не полностью, а по её части оценку не ставим.",
  cicd_no_runs: "Прогонов CI ещё не было. Это не значит, что CI не нужен.",
  cicd_no_automated_runs_in_period: "За полгода не было автоматических прогонов: ручные запуски в оценку не идут.",
  cicd_too_few_outcome_runs: "Для оценки нужно хотя бы 5 завершённых автоматических прогонов.",
  // Code health — backend/app/analyzers/code_health.py.
  code_health_scan_limit_exceeded: "Кода оказалось больше, чем проверяем за один анализ, а по части файлов оценку не ставим.",
  code_health_unreadable_source: "Часть файлов кода не удалось прочитать, а по части кода оценку не ставим.",
  code_files_unavailable: "Файлов кода на поддерживаемых языках не нашлось — оценивать нечего.",
  // Activity — история коммитов для недель с коммитами (docs/scoring-methodology.md, §3).
  commit_history_unavailable: "Историю коммитов прочитать не удалось, поэтому недели с коммитами в этот раз не посчитаны.",
  // Общие причины runner и ядра анализа.
  analyzer_not_configured: "Проверку этой части ещё не подключили — с ней самой всё может быть в порядке.",
  analyzer_execution_failed: `При проверке этой части произошёл сбой. ${RETRY}`,
  analyzer_category_mismatch: "Проверка вернула данные не той части проекта, поэтому результат не засчитан.",
  analysis_execution_failed: "Анализ завершился с ошибкой.",
  repository_mismatch: "Данные пришли по другому репозиторию.",
  empty_repository: "В репозитории ещё нет файлов и истории — оценивать нечего.",
};

/** Машинный код: строчная латиница и цифры, слова через «_» или «-». */
const MACHINE_CODE = /^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*$/;

type ClientError = [pattern: RegExp, explain: (match: RegExpMatchArray) => string];

/** Сообщения исключений SourceCraftClient. Последнее правило — для остальных его ошибок. */
const clientErrors: ClientError[] = [
  [/denied access with HTTP (\d{3})/i, (match) => `SourceCraft не дал доступ к этим данным (HTTP ${match[1]}).`],
  [/rate limit/i, () => `SourceCraft временно ограничил число запросов. ${RETRY}`],
  [/timed out/i, () => `SourceCraft не ответил вовремя. ${RETRY}`],
  [/network request failed/i, () => `Не удалось связаться с SourceCraft. ${RETRY}`],
  [/unexpected HTTP (\d{3})/i, (match) => `SourceCraft ответил ошибкой (HTTP ${match[1]}). ${RETRY}`],
  [/\bSourceCraft\b/i, () => "SourceCraft вернул данные в неожиданном виде."],
];

export function describeReason(reason: string): string {
  const text = reason.trim();
  const known = reasonTexts[text];
  if (known) {
    return known;
  }
  if (MACHINE_CODE.test(text)) {
    return `Причина: ${text}`;
  }
  // Activity и Issues склеивают несколько ошибок через «; » — одинаковые показываем один раз.
  const sentences = text
    .split(/;\s*/)
    .map((part) => part.trim())
    .filter(Boolean)
    .map(explainPart);
  return [...new Set(sentences)].join(" ");
}

export function isKnownReason(code: string): boolean {
  return code in reasonTexts;
}

/**
 * Объяснение причины к summary категории без повтора. Анализаторы иногда пишут в reason
 * продолжение summary: «Не удалось получить содержимое репозитория.» и «Не удалось получить
 * содержимое репозитория: git-команда завершилась ошибкой.». Тогда остаётся только то,
 * что reason добавляет. null — добавить нечего.
 */
export function reasonDetail(summary: string, reason: string | null): string | null {
  if (reason === null || reason.trim() === "") {
    return null;
  }
  const detail = describeReason(reason);
  const core = withoutFinalPunctuation(summary);
  const plainSummary = core.toLowerCase();
  const plainDetail = withoutFinalPunctuation(detail).toLowerCase();

  if (plainDetail === "" || plainSummary.includes(plainDetail)) {
    return null;
  }
  if (plainSummary !== "" && plainDetail.startsWith(plainSummary)) {
    const rest = detail.trim().slice(core.length).replace(/^[\s:;,.—–-]+/, "");
    return rest === "" ? null : asSentence(rest);
  }
  return detail;
}

function explainPart(part: string): string {
  // Activity кладёт в тот же список и коды: «…HTTP 403; commit_history_unavailable».
  const known = reasonTexts[part];
  if (known) {
    return known;
  }
  if (MACHINE_CODE.test(part)) {
    return `Причина: ${part}.`;
  }
  // Русская фраза уже написана для людей.
  if (/[а-яё]/i.test(part)) {
    return asSentence(part);
  }
  for (const [pattern, explain] of clientErrors) {
    const match = part.match(pattern);
    if (match) {
      return explain(match);
    }
  }
  return asSentence(part);
}

function withoutFinalPunctuation(text: string): string {
  return text.trim().replace(/[.!?…]+$/, "");
}

function asSentence(text: string): string {
  const sentence = text.charAt(0).toUpperCase() + text.slice(1);
  return /[.!?…]$/.test(sentence) ? sentence : `${sentence}.`;
}
