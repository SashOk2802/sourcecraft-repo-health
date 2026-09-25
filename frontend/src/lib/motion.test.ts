import { describe, expect, it } from "vitest";

import { countUpValue, easeOutCubic, tiltAngles } from "./motion";

describe("easeOutCubic", () => {
  it("идёт от нуля к единице", () => {
    expect(easeOutCubic(0)).toBe(0);
    expect(easeOutCubic(1)).toBe(1);
  });

  it("к середине уже проходит больше половины пути", () => {
    expect(easeOutCubic(0.5)).toBeGreaterThan(0.5);
  });

  it("не выходит за границы при кривом прогрессе", () => {
    expect(easeOutCubic(-1)).toBe(0);
    expect(easeOutCubic(2)).toBe(1);
  });
});

describe("countUpValue", () => {
  it("начинается со старта и заканчивается целью", () => {
    expect(countUpValue(0, 73, 0)).toBe(0);
    expect(countUpValue(0, 73, 1)).toBe(73);
  });

  it("считает и вниз", () => {
    expect(countUpValue(73, 0, 1)).toBe(0);
  });
});

describe("tiltAngles", () => {
  const box = { width: 200, height: 100 };

  it("в центре карточка не наклонена", () => {
    expect(tiltAngles(box, 100, 50)).toEqual({ rotateX: 0, rotateY: 0 });
  });

  it("у краёв наклон не больше заданного", () => {
    expect(tiltAngles(box, 200, 100, 4)).toEqual({ rotateX: -4, rotateY: 4 });
    expect(tiltAngles(box, 0, 0, 4)).toEqual({ rotateX: 4, rotateY: -4 });
  });

  it("курсор за пределами карточки не раскручивает наклон", () => {
    expect(tiltAngles(box, 1000, -1000, 4)).toEqual({ rotateX: 4, rotateY: 4 });
  });

  it("на нулевой карточке наклона нет", () => {
    expect(tiltAngles({ width: 0, height: 0 }, 10, 10)).toEqual({ rotateX: 0, rotateY: 0 });
  });
});
