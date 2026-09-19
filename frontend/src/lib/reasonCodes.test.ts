import { describe, expect, it } from "vitest";

import { describeReason, isKnownReason } from "./reasonCodes";

describe("describeReason", () => {
  it("переводит известные коды backend", () => {
    expect(describeReason("appsec_not_available")).toContain("AppSec");
    expect(isKnownReason("analyzer_not_configured")).toBe(true);
  });

  it("не прячет незнакомый код", () => {
    expect(describeReason("some_new_code")).toBe("Причина: some_new_code");
    expect(isKnownReason("some_new_code")).toBe(false);
  });
});
