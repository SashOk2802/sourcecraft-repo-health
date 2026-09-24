import { describe, expect, it } from "vitest";

import { getScoreBand } from "./scoreBands";

describe("getScoreBand", () => {
  it("делит шкалу на три полосы по границам 60 и 80", () => {
    expect(getScoreBand(0)).toBe("low");
    expect(getScoreBand(59.9)).toBe("low");
    expect(getScoreBand(60)).toBe("mid");
    expect(getScoreBand(79.9)).toBe("mid");
    expect(getScoreBand(80)).toBe("high");
    expect(getScoreBand(100)).toBe("high");
  });
});
