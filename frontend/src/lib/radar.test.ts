import { describe, expect, it } from "vitest";

import type { ReportCategory } from "../api/report";
import { buildRadar, toPoints } from "./radar";

function measured(code: string, score: number, weight: number): ReportCategory {
  return {
    code,
    label: code,
    status: "measured",
    score,
    weight,
    effectiveWeight: weight,
    points: (score * weight) / 100,
    summary: "",
    reason: null,
    evidence: [],
  };
}

const unavailable: ReportCategory = {
  code: "security",
  label: "Безопасность",
  status: "unavailable",
  score: null,
  weight: 25,
  effectiveWeight: null,
  points: null,
  summary: "Результаты AppSec не получены.",
  reason: "appsec_not_available",
  evidence: [],
};

// Тот же набор, что в отчёте gorod-dev/transit-api.
const categories: ReportCategory[] = [
  unavailable,
  measured("cicd", 58, 20),
  measured("documentation", 88, 20),
  measured("activity", 91, 15),
  measured("issues", 58, 15),
  measured("code_health", 70, 5),
];

const distance = (from: { x: number; y: number }, to: { x: number; y: number }): number =>
  Math.hypot(to.x - from.x, to.y - from.y);

describe("buildRadar", () => {
  it("ничего не рисует, когда рисовать нечего", () => {
    expect(buildRadar([])).toBeNull();
    expect(buildRadar([measured("a", 50, 10), measured("b", 50, 10)])).toBeNull();
    expect(buildRadar([measured("a", 50, 0), measured("b", 50, 0), measured("c", 50, 0)])).toBeNull();
  });

  it("длина луча пропорциональна весу категории", () => {
    const radar = buildRadar(categories, { radius: 100 });
    expect(radar).not.toBeNull();
    const byCode = Object.fromEntries(radar!.axes.map((axis) => [axis.code, axis]));

    // Самый тяжёлый вес занимает весь радиус, остальные — свою долю от него.
    expect(distance(radar!.center, byCode.security.end)).toBeCloseTo(100);
    expect(distance(radar!.center, byCode.cicd.end)).toBeCloseTo(80);
    expect(distance(radar!.center, byCode.activity.end)).toBeCloseTo(60);
    expect(distance(radar!.center, byCode.code_health.end)).toBeCloseTo(20);
  });

  it("вершина стоит на луче по оценке категории", () => {
    const radar = buildRadar(categories, { radius: 100 });
    const cicd = radar!.axes.find((axis) => axis.code === "cicd");
    // Луч 80, оценка 58 → вершина на 46,4 от центра.
    expect(distance(radar!.center, cicd!.vertex!)).toBeCloseTo(46.4);
  });

  it("у категории без данных вершины нет и в контур она не входит", () => {
    const radar = buildRadar(categories);
    const security = radar!.axes.find((axis) => axis.code === "security");

    expect(security!.vertex).toBeNull();
    expect(security!.score).toBeNull();
    expect(radar!.vertices).toHaveLength(5);
  });

  it("контур разрывается пунктиром там, где категорию пропустили", () => {
    const radar = buildRadar(categories);
    const dashed = radar!.edges.filter((edge) => edge.dashed);

    // Пропущена одна категория, значит и разрыв ровно один.
    expect(radar!.edges).toHaveLength(5);
    expect(dashed).toHaveLength(1);
  });

  it("без пропусков контур сплошной", () => {
    const radar = buildRadar([measured("a", 50, 10), measured("b", 60, 20), measured("c", 70, 30)]);
    expect(radar!.edges.every((edge) => !edge.dashed)).toBe(true);
  });

  it("первый луч смотрит вверх", () => {
    const radar = buildRadar(categories, { radius: 100 });
    const first = radar!.axes[0];

    expect(first.tip.x).toBeCloseTo(radar!.center.x);
    expect(first.tip.y).toBeCloseTo(radar!.center.y - 100);
  });

  it("подпись уходит наружу и прижимается к своей стороне", () => {
    const radar = buildRadar(categories);
    const [top, upperRight] = radar!.axes;

    expect(top.labelSpot.anchor).toBe("middle");
    // У верхней половины вторая строка уходит вверх, чтобы не залезать на фигуру.
    expect(top.labelSpot.subAt.y).toBeLessThan(top.labelSpot.at.y);
    expect(upperRight.labelSpot.anchor).toBe("start");
  });
});

describe("toPoints", () => {
  it("собирает атрибут points с одним знаком после запятой", () => {
    expect(toPoints([{ x: 1.234, y: 5 }, { x: 10, y: 20.55 }])).toBe("1.2,5.0 10.0,20.6");
  });
});
