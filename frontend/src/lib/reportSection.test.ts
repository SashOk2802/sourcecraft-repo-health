import { describe, expect, it } from "vitest";

import { resolveReportSection } from "./reportSection";

const none = { origin: null, report: null, personalRun: false, unavailable: false };

describe("resolveReportSection", () => {
  it("отчёт закрытого или внутреннего репозитория относит к «Моим репозиториям», публичного — к рейтингу", () => {
    // badgeAvailable backend ставит только публичному репозиторию из каталога (#118).
    expect(resolveReportSection({ ...none, report: { badgeAvailable: false } })).toBe("myRepositories");
    expect(resolveReportSection({ ...none, report: { badgeAvailable: true } })).toBe("leaderboard");
  });

  it("остаётся в разделе, откуда пришли по ссылке", () => {
    // Свой анализ публичного репозитория открыли из кабинета — назад ведём в кабинет, а не в рейтинг.
    expect(resolveReportSection({ ...none, origin: "myRepositories", report: { badgeAvailable: true } })).toBe(
      "myRepositories",
    );
    expect(resolveReportSection({ ...none, origin: "leaderboard", report: { badgeAvailable: true } })).toBe(
      "leaderboard",
    );
  });

  it("ход и ошибка анализа — личный запуск, до отчёта раздел ещё неизвестен", () => {
    expect(resolveReportSection({ ...none, personalRun: true })).toBe("myRepositories");
    expect(resolveReportSection(none)).toBeNull();
    // Нет входа или анализ чужой — страница предлагает вернуться к рейтингу.
    expect(resolveReportSection({ ...none, unavailable: true })).toBe("leaderboard");
  });
});
