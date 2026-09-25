import { describe, expect, it } from "vitest";

import { toCurrentUser, toMyRepositories } from "./me";

describe("toCurrentUser", () => {
  it("показывает логин, когда backend отдаёт только id и login", () => {
    // Так отвечает GET /api/v1/me на main.
    expect(toCurrentUser({ id: "user-1", login: "alex" })).toEqual({
      id: "user-1",
      login: "alex",
      displayName: "alex",
      avatarUrl: null,
    });
  });

  it("берёт отображаемое имя, если backend его добавит", () => {
    expect(toCurrentUser({ id: "user-1", login: "alex", displayName: "Алексей" }).displayName).toBe("Алексей");
  });

  it("без логина не оставляет шапку пустой", () => {
    expect(toCurrentUser({ id: "user-1", login: "  " })).toMatchObject({ login: null, displayName: "Пользователь Яндекса" });
  });
});

describe("toMyRepositories", () => {
  it("разбирает плоскую запись каталога: id, название, организация, webUrl, ветка", () => {
    const { items } = toMyRepositories({
      items: [
        {
          id: "0190f3c2",
          name: "platform-api",
          organizationSlug: "team",
          repositorySlug: "platform-api",
          webUrl: "https://sourcecraft.dev/team/platform-api",
          language: "Go",
          lastAnalysis: {
            id: "analysis-1",
            status: "partial",
            score: 73.4,
            isPreliminary: true,
            finishedAt: "2026-09-25T12:30:00Z",
          },
        },
      ],
    });
    expect(items).toEqual([
      {
        repository: {
          id: "0190f3c2",
          organizationSlug: "team",
          repositorySlug: "platform-api",
          name: "team/platform-api",
          url: "https://sourcecraft.dev/team/platform-api",
          description: null,
          language: "Go",
          visibility: "public",
        },
        lastAnalysis: {
          id: "analysis-1",
          status: "partial",
          analyzedAt: "2026-09-25T12:30:00Z",
          score: 73.4,
          isPreliminary: true,
        },
        activeAnalysisId: null,
      },
    ]);
  });

  it("понимает организацию объектом, slug и массив без обёртки", () => {
    const { items } = toMyRepositories([{ id: "r-1", organization: { slug: "team" }, slug: "web" }]);
    expect(items[0].repository).toMatchObject({ id: "r-1", organizationSlug: "team", repositorySlug: "web", url: null });
    expect(items[0].lastAnalysis).toBeNull();
  });

  it("идущий анализ показывает как идущий, а не как результат", () => {
    const { items } = toMyRepositories({
      repositories: [{ id: "r-1", name: "team/web", lastAnalysis: { id: "analysis-2", status: "running" } }],
    });
    expect(items[0].repository).toMatchObject({ organizationSlug: "team", repositorySlug: "web" });
    expect(items[0].lastAnalysis).toBeNull();
    expect(items[0].activeAnalysisId).toBe("analysis-2");
  });

  it("принимает и вложенный формат из предложения к контракту", () => {
    const { items } = toMyRepositories({
      items: [
        {
          repository: { id: "r-1", organizationSlug: "team", repositorySlug: "web", visibility: "private" },
          lastAnalysis: null,
          activeAnalysisId: "analysis-3",
        },
      ],
    });
    expect(items[0].repository).toMatchObject({ id: "r-1", visibility: "private" });
    expect(items[0].activeAnalysisId).toBe("analysis-3");
  });

  it("запись без id пропускает: анализ запускается только по id из каталога", () => {
    expect(toMyRepositories({ items: [{ name: "team/web" }] }).items).toEqual([]);
    expect(toMyRepositories({}).items).toEqual([]);
  });
});
