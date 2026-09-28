import { describe, expect, it, vi } from "vitest";

import { createSourceRouter, isBackendMissing, isDemoId, parseDataMode } from "./dataSource";
import { ApiError, isRouteMissing } from "./http";

const routeMissing = new ApiError(404, "Not Found", { routeMissing: true });
const unreachable = new ApiError(0, "Сервер не отвечает");
const notFoundAnalysis = new ApiError(404, "Analysis not found.");

describe("parseDataMode", () => {
  it("понимает новые значения", () => {
    expect(parseDataMode("auto", undefined)).toBe("auto");
    expect(parseDataMode("api", undefined)).toBe("api");
    expect(parseDataMode("demo", undefined)).toBe("demo");
  });

  it("демо — только по явному VITE_USE_MOCKS=true, как в PR #90", () => {
    expect(parseDataMode(undefined, "true")).toBe("demo");
    expect(parseDataMode(undefined, "false")).toBe("api");
    expect(parseDataMode(undefined, "1")).toBe("api");
  });

  it("по умолчанию — настоящий API", () => {
    expect(parseDataMode(undefined, undefined)).toBe("api");
    expect(parseDataMode("что-то", undefined)).toBe("api");
  });

  it("VITE_DATA_SOURCE важнее VITE_USE_MOCKS", () => {
    expect(parseDataMode("auto", "true")).toBe("auto");
    expect(parseDataMode("api", "true")).toBe("api");
  });
});

describe("isRouteMissing", () => {
  it("отличает неизвестный маршрут FastAPI от своего 404 backend", () => {
    expect(isRouteMissing(404, { detail: "Not Found", json: true })).toBe(true);
    expect(isRouteMissing(404, { detail: "Analysis not found.", json: true })).toBe(false);
  });

  it("404 не от API — тоже нет маршрута", () => {
    expect(isRouteMissing(404, { detail: null, json: false })).toBe(true);
  });

  it("405 и 501 — метод не сделан, 403 — нет", () => {
    expect(isRouteMissing(405, { detail: "Method Not Allowed", json: true })).toBe(true);
    expect(isRouteMissing(501, { detail: null, json: false })).toBe(true);
    expect(isRouteMissing(403, { detail: "Repository access denied.", json: true })).toBe(false);
  });
});

describe("isBackendMissing", () => {
  it("включает демо, только когда раздела или backend нет", () => {
    expect(isBackendMissing(routeMissing)).toBe(true);
    expect(isBackendMissing(unreachable)).toBe(true);
    expect(isBackendMissing(new ApiError(502, "Bad Gateway"))).toBe(true);
  });

  it("настоящие ошибки не маскирует", () => {
    expect(isBackendMissing(notFoundAnalysis)).toBe(false);
    expect(isBackendMissing(new ApiError(403, "Repository access denied."))).toBe(false);
    expect(isBackendMissing(new ApiError(500, "Internal Server Error"))).toBe(false);
    expect(isBackendMissing(new Error("сбой"))).toBe(false);
  });
});

describe("isDemoId", () => {
  it("узнаёт демо по префиксу", () => {
    expect(isDemoId("demo-1008")).toBe(true);
    expect(isDemoId("analysis-2026-09-15")).toBe(false);
  });
});

describe("createSourceRouter", () => {
  const demo = () => "демо";

  it("auto: берёт живые данные, если backend ответил", async () => {
    const router = createSourceRouter("auto", { delayMs: 0 });
    await expect(router.liveOrDemo("leaderboard", async () => "живые", demo)).resolves.toBe("живые");
    expect(router.currentSource("leaderboard")).toBe("live");
  });

  it("auto: раздела нет — отдаёт демо и какое-то время не спрашивает backend", async () => {
    let time = 0;
    const router = createSourceRouter("auto", { delayMs: 0, now: () => time });
    const live = vi.fn(async (): Promise<string> => {
      throw routeMissing;
    });

    await expect(router.liveOrDemo("leaderboard", live, demo)).resolves.toBe("демо");
    expect(router.currentSource("leaderboard")).toBe("demo");

    time = 60_000;
    await router.liveOrDemo("leaderboard", live, demo);
    expect(live).toHaveBeenCalledTimes(1);

    // Через пять минут пробуем снова: вдруг backend уже отдаёт раздел.
    time = 5 * 60_000 + 1;
    await router.liveOrDemo("leaderboard", live, demo);
    expect(live).toHaveBeenCalledTimes(2);
  });

  it("auto: настоящую ошибку показывает, а не прячет за демо", async () => {
    const router = createSourceRouter("auto", { delayMs: 0 });
    const live = async (): Promise<string> => {
      throw notFoundAnalysis;
    };
    await expect(router.liveOrDemo("session", live, demo)).rejects.toBe(notFoundAnalysis);
  });

  it("api: демо не подставляет", async () => {
    const router = createSourceRouter("api", { delayMs: 0 });
    const live = async (): Promise<string> => {
      throw routeMissing;
    };
    await expect(router.liveOrDemo("methodology", live, demo)).rejects.toBe(routeMissing);
  });

  it("demo: backend не трогает", async () => {
    const router = createSourceRouter("demo", { delayMs: 0 });
    const live = vi.fn(async () => "живые");
    await expect(router.liveOrDemo("leaderboard", live, demo)).resolves.toBe("демо");
    expect(live).not.toHaveBeenCalled();
    expect(router.currentSource("session")).toBe("demo");
  });

  it("сообщает подписчикам о смене источника", async () => {
    const router = createSourceRouter("auto", { delayMs: 0 });
    const listener = vi.fn();
    const unsubscribe = router.subscribe(listener);

    await router.liveOrDemo("leaderboard", async () => "живые", demo);
    await router.liveOrDemo("leaderboard", async () => "снова живые", demo);
    expect(listener).toHaveBeenCalledTimes(1);

    unsubscribe();
    await router.liveOrDemo(
      "leaderboard",
      async () => {
        throw unreachable;
      },
      demo,
    );
    expect(listener).toHaveBeenCalledTimes(1);
  });
});
