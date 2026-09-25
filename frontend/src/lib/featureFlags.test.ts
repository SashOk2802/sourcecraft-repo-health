import { describe, expect, it } from "vitest";

import { isYandexAuthReady } from "./featureFlags";

describe("isYandexAuthReady", () => {
  it("включается явными значениями", () => {
    expect(isYandexAuthReady("true")).toBe(true);
    expect(isYandexAuthReady("1")).toBe(true);
  });

  it("выключен, пока переменная не задана", () => {
    expect(isYandexAuthReady(undefined)).toBe(false);
    expect(isYandexAuthReady("")).toBe(false);
  });

  it("не включается случайными значениями", () => {
    expect(isYandexAuthReady("false")).toBe(false);
    expect(isYandexAuthReady("0")).toBe(false);
    expect(isYandexAuthReady("yes")).toBe(false);
  });
});
