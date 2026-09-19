import { describe, expect, it } from "vitest";

import { analysisRunState } from "./analyses";
import { findMockRepository } from "./catalog";
import { MOCK_ANALYSIS_DURATION_MS } from "./runs";

const repository = findMockRepository("gorod-dev", "transit-api")!;
const run = { id: "an-test", repositoryId: repository.id, createdAt: 0 };

describe("ход mock-анализа", () => {
  it("идёт по этапам: очередь → сбор → расчёт → результат", () => {
    expect(analysisRunState(run, repository, 500).status).toBe("queued");
    expect(analysisRunState(run, repository, 4_000).status).toBe("collecting");
    expect(analysisRunState(run, repository, 9_000).status).toBe("calculating");
    expect(analysisRunState(run, repository, MOCK_ANALYSIS_DURATION_MS).status).toBe("partial");
  });

  it("отмечает текущий этап и не забегает вперёд", () => {
    const stages = analysisRunState(run, repository, 4_000).stages?.map((stage) => stage.status);
    expect(stages).toEqual(["done", "done", "running", "pending", "pending", "pending"]);
  });

  it("без данных AppSec отмечает этап и отдаёт предварительную оценку", () => {
    const state = analysisRunState(run, repository, MOCK_ANALYSIS_DURATION_MS);
    expect(state.stages?.find((stage) => stage.code === "appsec")?.status).toBe("unavailable");
    expect(state.isPreliminary).toBe(true);
    expect(state.score).toBe(73.4);
    expect(state.finishedAt).not.toBeNull();
  });

  it("полная оценка не помечается предварительной", () => {
    const full = findMockRepository("kvant-lab", "scheduler")!;
    const state = analysisRunState(
      { id: "an-full", repositoryId: full.id, createdAt: 0 },
      full,
      MOCK_ANALYSIS_DURATION_MS,
    );
    expect(state.status).toBe("completed");
    expect(state.isPreliminary).toBe(false);
  });
});
