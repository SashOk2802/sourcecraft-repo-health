import { describe, expect, it } from "vitest";

import {
  fetchAnalysisStatus,
  fetchMyRepositories,
  isAnalysisTerminal,
  startRepositoryAnalysis,
} from "./myRepositories";

describe("каталог и запуск анализа в mock-режиме", () => {
  it("отдаёт только публичные репозитории", async () => {
    const response = await fetchMyRepositories();

    expect(response.total).toBe(response.repositories.length);
    expect(response.repositories).not.toHaveLength(0);
    expect(response.repositories.every((repository) => repository.id.startsWith("repo-1"))).toBe(true);
    expect(response.repositories.some((repository) => repository.name === "shkola-it/homework-checker")).toBe(false);
  });

  it("проводит запуск через очередь до отчёта", async () => {
    const queued = await startRepositoryAnalysis("repo-1001");
    expect(queued.status).toBe("queued");
    expect(isAnalysisTerminal(queued.status)).toBe(false);

    const running = await fetchAnalysisStatus(queued.id);
    expect(running.status).toBe("running");

    const finished = await fetchAnalysisStatus(queued.id);
    expect(["completed", "partial"]).toContain(finished.status);
    expect(isAnalysisTerminal(finished.status)).toBe(true);
    expect(finished.reportUrl).toContain(encodeURIComponent(finished.id));
  });

  it("не считает активные статусы завершёнными", () => {
    expect(isAnalysisTerminal("collecting")).toBe(false);
    expect(isAnalysisTerminal("calculating")).toBe(false);
    expect(isAnalysisTerminal("failed")).toBe(true);
  });
});
