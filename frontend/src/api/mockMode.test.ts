import { describe, expect, it } from "vitest";

import { areMocksEnabled } from "./mockMode";

describe("mock-режим", () => {
  it("включается только явным VITE_USE_MOCKS=true", () => {
    expect(areMocksEnabled("true")).toBe(true);
    expect(areMocksEnabled(undefined)).toBe(false);
    expect(areMocksEnabled("false")).toBe(false);
    expect(areMocksEnabled("1")).toBe(false);
  });
});
