import { describe, expect, it } from "vitest";

import { parseThemePreference, readThemePreference, saveThemePreference, THEME_STORAGE_KEY } from "./theme";

function memoryStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
    removeItem: (key: string) => void data.delete(key),
    data,
  };
}

const brokenStorage = {
  getItem: () => {
    throw new Error("SecurityError");
  },
  setItem: () => {
    throw new Error("QuotaExceededError");
  },
  removeItem: () => {
    throw new Error("SecurityError");
  },
};

describe("parseThemePreference", () => {
  it("узнаёт три варианта", () => {
    expect(parseThemePreference("system")).toBe("system");
    expect(parseThemePreference("light")).toBe("light");
    expect(parseThemePreference("dark")).toBe("dark");
  });

  it("всё незнакомое считает «как в системе»", () => {
    expect(parseThemePreference(null)).toBe("system");
    expect(parseThemePreference("")).toBe("system");
    expect(parseThemePreference("dark-hc")).toBe("system");
  });
});

describe("readThemePreference", () => {
  it("читает сохранённый выбор", () => {
    expect(readThemePreference(memoryStorage({ [THEME_STORAGE_KEY]: "dark" }))).toBe("dark");
  });

  it("без хранилища или при его отказе следует системе", () => {
    expect(readThemePreference(null)).toBe("system");
    expect(readThemePreference(brokenStorage)).toBe("system");
  });
});

describe("saveThemePreference", () => {
  it("сохраняет явный выбор", () => {
    const storage = memoryStorage();
    saveThemePreference(storage, "light");
    expect(storage.data.get(THEME_STORAGE_KEY)).toBe("light");
  });

  it("«как в системе» не хранит, а стирает прежний выбор", () => {
    const storage = memoryStorage({ [THEME_STORAGE_KEY]: "dark" });
    saveThemePreference(storage, "system");
    expect(storage.data.has(THEME_STORAGE_KEY)).toBe(false);
  });

  it("отказ хранилища не роняет интерфейс", () => {
    expect(() => saveThemePreference(brokenStorage, "dark")).not.toThrow();
    expect(() => saveThemePreference(null, "dark")).not.toThrow();
  });
});
