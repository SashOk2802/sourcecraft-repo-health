import { describe, expect, it } from "vitest";

import { describeStartError } from "./analyses";
import { ApiError } from "./http";

describe("describeStartError", () => {
  it("объясняет отказы запуска по кодам контракта", () => {
    expect(describeStartError(new ApiError(403, "Repository access denied."))).toContain("только публичный");
    expect(describeStartError(new ApiError(404, "Repository not found."))).toContain("нет в каталоге");
    expect(describeStartError(new ApiError(503, "Analysis dispatch is not configured."))).toContain("не настроен");
  });

  it("429 и 502 — ограничения и ответы SourceCraft, а не наша ошибка", () => {
    // docs/api-contract.md после #20: 429 — лимит частоты SourceCraft, 502 — непригодный ответ SourceCraft.
    expect(describeStartError(new ApiError(429, "Too Many Requests"))).toContain("Попробуйте через минуту");
    expect(describeStartError(new ApiError(502, "Bad Gateway"))).toContain("SourceCraft вернул данные");
  });

  it("503 из-за недоступного каталога SourceCraft не выдаёт за ненастроенный сервер", () => {
    const busy = describeStartError(new ApiError(503, "SourceCraft repository catalog is unavailable."));
    expect(busy).toContain("SourceCraft");
    expect(busy).not.toContain("не настроен");
  });

  it("остальное объясняет общими словами", () => {
    expect(describeStartError(new ApiError(0, "Сервер не отвечает"))).toContain("Сервер не отвечает");
    expect(describeStartError(new Error("сбой"))).toBe("сбой");
  });
});
