import { describe, expect, it } from "vitest";

import { hashTarget } from "./useHashScroll";

describe("hashTarget", () => {
  it("достаёт id раздела из якоря", () => {
    expect(hashTarget("#no-data")).toBe("no-data");
    expect(hashTarget("#%D1%84%D0%BE%D1%80%D0%BC%D1%83%D0%BB%D0%B0")).toBe("формула");
  });

  it("без якоря — null, битый %-код — как есть", () => {
    expect(hashTarget("")).toBeNull();
    expect(hashTarget("#")).toBeNull();
    expect(hashTarget("#%E0%A4%A")).toBe("%E0%A4%A");
  });
});
