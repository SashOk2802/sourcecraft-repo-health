import { describe, expect, it } from "vitest";

import { describeStartError } from "./analyses";
import { ApiError } from "./http";

describe("describeStartError", () => {
  it("объясняет отказы запуска по кодам контракта", () => {
    expect(describeStartError(new ApiError(403, "Repository access denied."))).toContain("только публичный");
    expect(describeStartError(new ApiError(404, "Repository not found."))).toContain("нет в каталоге");
    expect(describeStartError(new ApiError(503, "Analysis dispatch is not configured."))).toContain("не настроен");
    expect(describeStartError(new ApiError(429, "Too Many Requests"))).toContain("15 минут");
  });

  it("остальное объясняет общими словами", () => {
    expect(describeStartError(new ApiError(0, "Сервер не отвечает"))).toContain("Сервер не отвечает");
    expect(describeStartError(new Error("сбой"))).toBe("сбой");
  });
});
