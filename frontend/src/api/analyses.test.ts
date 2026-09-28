import { describe, expect, it } from "vitest";

import { describeStartError } from "./analyses";
import { ApiError, describeError } from "./http";

describe("describeStartError", () => {
  it("объясняет отказы запуска по кодам контракта", () => {
    expect(describeStartError(new ApiError(403, "Repository access denied."))).toContain("Нет доступа");
    expect(describeStartError(new ApiError(404, "Repository not found."))).toContain("нет в каталоге");
    expect(describeStartError(new ApiError(503, "Analysis dispatch is not configured."))).toContain("не настроен");
  });

  it("403 от защиты CSRF не выдаёт за закрытый репозиторий", () => {
    // Так backend (PR #52) отвечает на POST с чужого адреса.
    const rejected = describeStartError(new ApiError(403, "Cross-site request rejected."));
    expect(rejected).toContain("основному адресу");
    expect(rejected).not.toContain("подключению SourceCraft");
  });

  it("закрытый репозиторий без подключения — предлагает подключить SourceCraft", () => {
    // backend/app/main.py после PR #101.
    const required = describeStartError(new ApiError(409, "Connect SourceCraft to analyze this repository."));
    expect(required).toContain("подключите SourceCraft");
  });

  it("503 из-за подключения не выдаёт за ненастроенный сервер", () => {
    const connection = describeStartError(new ApiError(503, "SourceCraft connection is unavailable."));
    expect(connection).toContain("по вашему подключению");
    expect(connection).not.toContain("не настроен");
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

describe("describeError и личное подключение SourceCraft", () => {
  it("401 из-за токена SourceCraft не выдаёт за выход из Яндекс ID", () => {
    // GET /api/v1/me/repositories: сохранённый токен отозван или истёк.
    const renew = describeError(new ApiError(401, "SourceCraft connection must be renewed."));
    expect(renew).toContain("подключите заново");
    expect(renew).not.toContain("Яндекс ID");
    // POST /api/v1/connections/sourcecraft: SourceCraft не принял токен.
    const rejected = describeError(new ApiError(401, "SourceCraft rejected the token."));
    expect(rejected).toContain("не принял токен");
    expect(rejected).not.toContain("Яндекс ID");
    // Настоящий выход из сессии — по-прежнему про вход.
    expect(describeError(new ApiError(401, "Authentication required."))).toContain("Яндекс ID");
  });

  it("объясняет формат токена, недоступный SourceCraft и выключенное подключение", () => {
    expect(describeError(new ApiError(422, "SourceCraft token has an invalid format."))).toContain("не похоже на токен");
    expect(describeError(new ApiError(503, "SourceCraft token could not be verified right now."))).toContain(
      "не может проверить токен",
    );
    expect(describeError(new ApiError(404, "SourceCraft connection is not configured."))).toContain("не настроено");
  });
});
