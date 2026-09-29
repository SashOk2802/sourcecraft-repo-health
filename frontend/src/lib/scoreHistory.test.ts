import { describe, expect, it } from "vitest";

import { toScorePoints, type ScorePoint } from "../api/publicHistory";
import { chartSegments, summarizeHistory } from "./scoreHistory";

const point = (day: number, score: number | null, version = "v2", status: ScorePoint["status"] = "completed"): ScorePoint => ({
  analyzedAt: `2026-09-${String(day).padStart(2, "0")}T12:00:00Z`,
  score,
  coverage: score === null ? null : 1,
  status,
  methodologyVersion: version,
});

describe("summarizeHistory", () => {
  it("считает изменение только внутри последней версии методики", () => {
    const summary = summarizeHistory([point(1, 90, "v1"), point(2, 60), point(3, null), point(4, 72)]);
    expect(summary.latestVersion).toBe("v2");
    expect(summary.comparable.map((item) => item.score)).toEqual([60, 72]);
    expect(summary.delta).toBe(12);
    expect(summary.versionChanged).toBe(true);
  });

  it("пустая история и одна точка — без изменения", () => {
    expect(summarizeHistory([])).toMatchObject({ delta: null, latestVersion: null, versionChanged: false });
    expect(summarizeHistory([point(1, 70)]).delta).toBeNull();
  });
});

describe("chartSegments", () => {
  it("рвёт линию на точке без Score и не рисует ноль", () => {
    const segments = chartSegments([point(1, 50), point(2, null), point(3, 100)], 120, 100, 10);
    expect(segments).toHaveLength(2);
    expect(segments[0][0]).toMatchObject({ x: 10, y: 50 });
    expect(segments[1][0]).toMatchObject({ x: 110, y: 10 });
  });
});

describe("toScorePoints", () => {
  it("берёт только ожидаемые поля и пропускает испорченные точки", () => {
    const points = toScorePoints({
      points: [
        { analyzedAt: "2026-09-01T12:00:00Z", score: 70.5, coverage: 0.8, status: "partial", methodologyVersion: "v2", extra: 1 },
        { analyzedAt: "not-a-date", score: 1, coverage: 1, status: "completed", methodologyVersion: "v2" },
        { analyzedAt: "2026-09-02T12:00:00Z", score: "80", coverage: 1, status: "completed", methodologyVersion: "v2" },
      ],
    });
    expect(points).toEqual([
      { analyzedAt: "2026-09-01T12:00:00Z", score: 70.5, coverage: 0.8, status: "partial", methodologyVersion: "v2" },
    ]);
    expect(toScorePoints(null)).toEqual([]);
  });
});
