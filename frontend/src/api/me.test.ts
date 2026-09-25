import { describe, expect, it } from "vitest";

import { toCurrentUser } from "./me";

describe("toCurrentUser", () => {
  it("показывает логин, когда backend отдаёт только id и login", () => {
    // Так отвечает GET /api/v1/me на main.
    expect(toCurrentUser({ id: "user-1", login: "alex" })).toEqual({
      id: "user-1",
      login: "alex",
      displayName: "alex",
      avatarUrl: null,
    });
  });

  it("берёт отображаемое имя, если backend его добавит", () => {
    expect(toCurrentUser({ id: "user-1", login: "alex", displayName: "Алексей" }).displayName).toBe("Алексей");
  });

  it("без логина не оставляет шапку пустой", () => {
    expect(toCurrentUser({ id: "user-1", login: "  " })).toMatchObject({ login: null, displayName: "Пользователь Яндекса" });
  });
});
