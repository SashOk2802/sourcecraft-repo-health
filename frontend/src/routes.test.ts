import { describe, expect, it } from "vitest";

import { matchRoute, paths } from "./routes";

describe("matchRoute", () => {
  it("узнаёт все страницы", () => {
    expect(matchRoute("/")).toEqual({ page: "leaderboard" });
    expect(matchRoute("/repositories/team/platform-api")).toEqual({
      page: "report",
      organizationSlug: "team",
      repositorySlug: "platform-api",
    });
    expect(matchRoute("/me/repositories")).toEqual({ page: "myRepositories" });
    expect(matchRoute("/analyses/an-42")).toEqual({ page: "analysis", analysisId: "an-42" });
    expect(matchRoute("/methodology/")).toEqual({ page: "methodology" });
  });

  it("не путает похожие адреса", () => {
    expect(matchRoute("/repositories/team")).toEqual({ page: "notFound" });
    expect(matchRoute("/repositories/team/api/extra")).toEqual({ page: "notFound" });
    expect(matchRoute("/me")).toEqual({ page: "notFound" });
  });

  it("не падает на битом %-коде", () => {
    expect(matchRoute("/analyses/%E0%A4%A")).toEqual({ page: "analysis", analysisId: "%E0%A4%A" });
  });

  it("разбирает адрес, собранный через paths", () => {
    const path = paths.report("org with space", "repo/slash");
    expect(matchRoute(path)).toEqual({
      page: "report",
      organizationSlug: "org with space",
      repositorySlug: "repo/slash",
    });
  });
});
