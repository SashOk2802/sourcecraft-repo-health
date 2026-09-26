import { describe, expect, it } from "vitest";

import type { ReportCategory } from "../api/report";
import { buildRadar, describeAxis, toPoints } from "./radar";

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

const notApplicable = (code: string, weight: number): ReportCategory => ({
  ...unavailable,
  code,
  label: code,
  status: "not_applicable",
  weight,
  reason: null,
});

describe("статус категории на радаре", () => {
  it("ось помнит статус, который прислал backend", () => {
    const radar = buildRadar([...categories.slice(0, 4), notApplicable("issues", 15), categories[5]]);
    const byCode = Object.fromEntries(radar!.axes.map((axis) => [axis.code, axis]));

    expect(byCode.security.status).toBe("unavailable");
    expect(byCode.issues.status).toBe("not_applicable");
    expect(byCode.issues.vertex).toBeNull();
    expect(byCode.cicd.status).toBe("measured");
  });

  it("«не применимо» подписано отдельно от «нет данных»", () => {
    const radar = buildRadar([...categories.slice(0, 4), notApplicable("issues", 15), categories[5]]);
    const byCode = Object.fromEntries(radar!.axes.map((axis) => [axis.code, axis]));

    expect(describeAxis(byCode.issues)).toBe("вес 15% · не применимо");
    expect(describeAxis(byCode.security)).toBe("вес 25% · нет данных");
    expect(describeAxis(byCode.cicd)).toBe("вес 20% · оценка 58");
  });

  it("«мало данных» и сбой тоже названы своими словами", () => {
    const insufficient: ReportCategory = { ...unavailable, code: "activity", status: "insufficient_sample", weight: 15 };
    const failed: ReportCategory = { ...unavailable, code: "cicd", status: "error", weight: 20 };
    const radar = buildRadar([measured("a", 50, 10), insufficient, failed]);
    const byCode = Object.fromEntries(radar!.axes.map((axis) => [axis.code, axis]));

    expect(describeAxis(byCode.activity)).toBe("вес 15% · мало данных");
    expect(describeAxis(byCode.cicd)).toBe("вес 20% · ошибка сбора");
  });
});

describe("заливка радара", () => {
  const m = (code: string) => measured(code, 60, 10);
  const gap = (code: string): ReportCategory => ({ ...unavailable, code, label: code, weight: 10 });

  it("без пропусков — один многоугольник по всем вершинам", () => {
    const radar = buildRadar([m("a"), m("b"), m("c")]);
    expect(radar!.fills).toHaveLength(1);
    expect(radar!.fills[0]).toEqual(radar!.vertices);
  });

  it("сектор категории без оценки не закрывает: веер от центра по соседним вершинам", () => {
    // Отчёт transit-api: безопасность без данных — первый луч.
    const radar = buildRadar(categories);
    expect(radar!.fills).toHaveLength(1);
    const [fill] = radar!.fills;
    expect(fill[0]).toEqual(radar!.center);
    expect(fill.slice(1)).toEqual(radar!.vertices);
  });

  it("два пропуска делят заливку на отдельные веера", () => {
    const radar = buildRadar([m("a"), m("b"), gap("c"), m("d"), m("e"), gap("f")]);
    const byCode = Object.fromEntries(radar!.axes.map((axis) => [axis.code, axis.vertex]));
    // Порядок вееров для отрисовки не важен — важно, что их два и сектора c и f пустые.
    expect(radar!.fills).toHaveLength(2);
    expect(radar!.fills).toContainEqual([radar!.center, byCode.a, byCode.b]);
    expect(radar!.fills).toContainEqual([radar!.center, byCode.d, byCode.e]);
  });

  it("серия может идти через стык последнего и первого луча", () => {
    const radar = buildRadar([m("a"), gap("b"), m("c"), m("d"), gap("e"), m("f")]);
    const byCode = Object.fromEntries(radar!.axes.map((axis) => [axis.code, axis.vertex]));
    expect(radar!.fills).toHaveLength(2);
    expect(radar!.fills).toContainEqual([radar!.center, byCode.c, byCode.d]);
    // f и a — соседи через стык последнего и первого луча.
    expect(radar!.fills).toContainEqual([radar!.center, byCode.f, byCode.a]);
  });

  it("одна измеренная вершина площади не даёт — заливки нет", () => {
    expect(buildRadar([m("a"), gap("b"), gap("c")])!.fills).toEqual([]);
  });

  it("«не применимо» — тоже разрыв заливки", () => {
    const radar = buildRadar([m("a"), m("b"), notApplicable("c", 10), m("d")]);
    const byCode = Object.fromEntries(radar!.axes.map((axis) => [axis.code, axis.vertex]));
    expect(radar!.fills).toEqual([[radar!.center, byCode.d, byCode.a, byCode.b]]);
  });
});

describe("toPoints", () => {
  it("собирает атрибут points с одним знаком после запятой", () => {
    expect(toPoints([{ x: 1.234, y: 5 }, { x: 10, y: 20.55 }])).toBe("1.2,5.0 10.0,20.6");
  });
});
