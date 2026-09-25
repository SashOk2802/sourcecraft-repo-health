import { describe, expect, it } from "vitest";

import { describeReason, isKnownReason, reasonDetail } from "./reasonCodes";

describe("describeReason", () => {
  it("переводит известные коды backend", () => {
    expect(describeReason("appsec_not_available")).toContain("не значит, что уязвимостей нет");
    expect(isKnownReason("analyzer_not_configured")).toBe(true);
  });

  it("знает причины анализаторов Security, CI/CD и Code health", () => {
    const codes = [
      "appsec_unavailable",
      "appsec_source_error",
      "security_scoring_not_configured",
      "empty_repository",
      "cicd_runs_unavailable",
      "cicd_runs_truncated",
      "cicd_no_runs",
      "cicd_no_automated_runs_in_period",
      "cicd_too_few_outcome_runs",
      "code_health_scan_limit_exceeded",
      "code_files_unavailable",
    ];
    for (const code of codes) {
      expect(isKnownReason(code)).toBe(true);
    }
  });

  it("не повторяет summary backend о неподключённом анализаторе", () => {
    // Backend уже пишет «Анализатор категории пока не подключён.» — здесь объясняем, что это значит.
    expect(describeReason("analyzer_not_configured")).not.toContain("Анализатор");
  });

  it("не прячет незнакомый код", () => {
    expect(describeReason("some_new_code")).toBe("Причина: some_new_code");
    expect(isKnownReason("some_new_code")).toBe(false);
  });

  it("русскую фразу анализатора показывает как есть", () => {
    // backend/app/analyzers/issues.py и code_health.py пишут в reason готовый текст.
    expect(describeReason("Трекер задач не используется.")).toBe("Трекер задач не используется.");
    expect(describeReason("Отсутствуют файлы исходного кода поддерживаемых языков.")).toBe(
      "Отсутствуют файлы исходного кода поддерживаемых языков.",
    );
  });

  it("ошибки клиента SourceCraft пересказывает по-русски", () => {
    expect(describeReason("SourceCraft denied access with HTTP 403")).toBe(
      "SourceCraft не дал доступ к этим данным (HTTP 403).",
    );
    expect(describeReason("SourceCraft request timed out")).toContain("не ответил вовремя");
    expect(describeReason("SourceCraft network request failed")).toContain("Не удалось связаться с SourceCraft");
    expect(describeReason("SourceCraft rate limit exceeded")).toContain("ограничил число запросов");
    expect(describeReason("SourceCraft returned unexpected HTTP 502")).toContain("HTTP 502");
    expect(describeReason("SourceCraft returned invalid JSON")).toBe("SourceCraft вернул данные в неожиданном виде.");
  });

  it("склеенные ошибки показывает по одному разу", () => {
    // Так activity.py собирает reason: ошибки списков через «; », у issues — с префиксом статуса.
    const reason = "SourceCraft denied access with HTTP 403; open: SourceCraft denied access with HTTP 403";
    expect(describeReason(reason)).toBe("SourceCraft не дал доступ к этим данным (HTTP 403).");
  });
});

describe("reasonDetail", () => {
  it("объясняет код причины к summary", () => {
    expect(reasonDetail("Результаты AppSec не получены.", "appsec_unavailable")).toContain("не значит");
  });

  it("не повторяет summary, если reason — его продолжение", () => {
    // backend/app/analysis/providers.py при ошибке git-клона.
    expect(
      reasonDetail(
        "Не удалось получить содержимое репозитория.",
        "Не удалось получить содержимое репозитория: git-команда завершилась ошибкой.",
      ),
    ).toBe("Git-команда завершилась ошибкой.");
  });

  it("молчит, если reason ничего не добавляет", () => {
    expect(reasonDetail("Трекер задач не используется.", "Трекер задач не используется.")).toBeNull();
    expect(reasonDetail("Данные CI не получены.", null)).toBeNull();
    expect(reasonDetail("Данные CI не получены.", "  ")).toBeNull();
  });
});
