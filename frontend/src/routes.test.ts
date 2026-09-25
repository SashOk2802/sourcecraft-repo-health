import { describe, expect, it } from "vitest";

import { matchRoute, paths } from "./routes";

describe("matchRoute", () => {
  it("узнаёт все страницы", () => {
    expect(matchRoute("/")).toEqual({ page: "leaderboard" });
    expect(matchRoute("/analyses/analysis-2026-09-15")).toEqual({
      page: "analysis",
      analysisId: "analysis-2026-09-15",
    });
    expect(matchRoute("/me/repositories")).toEqual({ page: "myRepositories" });
    expect(matchRoute("/methodology/")).toEqual({ page: "methodology" });
  });

  it("не путает похожие адреса", () => {
    expect(matchRoute("/analyses")).toEqual({ page: "notFound" });
    expect(matchRoute("/analyses/an-1/report")).toEqual({ page: "notFound" });
    expect(matchRoute("/me")).toEqual({ page: "notFound" });
  });

  it("не падает на битом %-коде", () => {
    expect(matchRoute("/analyses/%E0%A4%A")).toEqual({ page: "analysis", analysisId: "%E0%A4%A" });
  });

  it("разбирает адрес, собранный через paths", () => {
    expect(matchRoute(paths.analysis("an 42/x"))).toEqual({ page: "analysis", analysisId: "an 42/x" });
  });
});
