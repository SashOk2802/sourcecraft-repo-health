import { describe, expect, it } from "vitest";

import type { AnalysisStatusResponse } from "../api/analyses";
import type { MyRepository } from "../api/me";
import { forgetAnalysis, recentAnalysisId, rememberAnalysis, withRecentAnalysis } from "./recentAnalyses";

function memoryStorage(initial: Record<string, string> = {}): Storage {
  const values = new Map(Object.entries(initial));
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    key: (index) => [...values.keys()][index] ?? null,
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => void values.set(key, value),
    removeItem: (key) => void values.delete(key),
  };
}

describe("память запущенных анализов", () => {
  it("помнит последний анализ каждого репозитория", () => {
    const storage = memoryStorage();
    rememberAnalysis("repo-1", "analysis-1", storage);
    rememberAnalysis("repo-2", "analysis-2", storage);
    rememberAnalysis("repo-1", "analysis-3", storage);
    expect(recentAnalysisId("repo-1", storage)).toBe("analysis-3");
    expect(recentAnalysisId("repo-2", storage)).toBe("analysis-2");
    expect(recentAnalysisId("repo-3", storage)).toBeNull();
  });

  it("забывает анализ, которого backend не знает", () => {
    const storage = memoryStorage();
    rememberAnalysis("repo-1", "analysis-1", storage);
    forgetAnalysis("repo-1", storage);
    expect(recentAnalysisId("repo-1", storage)).toBeNull();
  });

  it("без хранилища и с испорченными данными просто ничего не помнит", () => {
    expect(() => rememberAnalysis("repo-1", "analysis-1", null)).not.toThrow();
    expect(recentAnalysisId("repo-1", null)).toBeNull();
    expect(recentAnalysisId("repo-1", memoryStorage({ "rh-recent-analyses": "{не json" }))).toBeNull();
    expect(recentAnalysisId("repo-1", memoryStorage({ "rh-recent-analyses": "[1, 2]" }))).toBeNull();
  });
});

const item: MyRepository = {
  repository: {
    id: "repo-1",
    organizationSlug: "k-5-45mm",
    repositorySlug: "dozzle-plus",
    name: "k-5-45mm/dozzle-plus",
    url: null,
    description: null,
    language: "Go",
    visibility: "public",
    isEmpty: false,
  },
  lastAnalysis: null,
  activeAnalysisId: null,
};

function status(overrides: Partial<AnalysisStatusResponse>): AnalysisStatusResponse {
  return {
    id: "analysis-1",
    status: "partial",
    repository: { id: "repo-1" },
    score: 75.3,
    isPreliminary: true,
    reportUrl: "/api/v1/analyses/analysis-1/report",
    markdownReportUrl: "/api/v1/analyses/analysis-1/report.md",
    createdAt: "2026-09-27T10:20:00Z",
    finishedAt: "2026-09-27T10:23:00Z",
    ...overrides,
  };
}

describe("withRecentAnalysis", () => {
  it("завершённый анализ из памяти показывает как последний: дата, Score, отчёт", () => {
    expect(withRecentAnalysis(item, status({})).lastAnalysis).toEqual({
      id: "analysis-1",
      status: "partial",
      analyzedAt: "2026-09-27T10:23:00Z",
      score: 75.3,
      isPreliminary: true,
    });
  });

  it("идущий анализ показывает ссылкой на ход, а не результатом", () => {
    const running = withRecentAnalysis(item, status({ status: "running", score: null, finishedAt: null }));
    expect(running.activeAnalysisId).toBe("analysis-1");
    expect(running.lastAnalysis).toBeNull();
  });

  it("данные backend важнее памяти браузера", () => {
    const fromBackend: MyRepository = {
      ...item,
      lastAnalysis: { id: "analysis-0", status: "completed", analyzedAt: null, score: 80, isPreliminary: false },
    };
    expect(withRecentAnalysis(fromBackend, status({}))).toBe(fromBackend);
    expect(withRecentAnalysis(item, null)).toBe(item);
  });

  it("завершение опрошенного активного анализа заменяет устаревший activeAnalysisId", () => {
    const active = { ...item, activeAnalysisId: "analysis-1" };
    const completed = withRecentAnalysis(active, status({ status: "completed", isPreliminary: false, score: 82 }));
    expect(completed.activeAnalysisId).toBeNull();
    expect(completed.lastAnalysis).toMatchObject({ id: "analysis-1", status: "completed", score: 82 });
  });
});
