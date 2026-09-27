import { describe, expect, it } from "vitest";

import {
  formatBytes,
  formatDate,
  formatDateTime,
  formatDateTimeCompact,
  formatDuration,
  formatInteger,
  formatLikes,
  formatRelativeDay,
  formatScore,
  formatShare,
  plural,
} from "./format";

const NBSP = " ";

describe("plural", () => {
  it("выбирает форму по правилам русского языка", () => {
    const days = (n: number): string => plural(n, "день", "дня", "дней");
    expect([1, 2, 5, 11, 12, 21, 22, 25, 111, 104].map(days)).toEqual([
      "день",
      "дня",
      "дней",
      "дней",
      "дней",
      "день",
      "дня",
      "дней",
      "дней",
      "дня",
    ]);
  });
});

describe("числа", () => {
  it("разбивает разряды неразрывным пробелом", () => {
    expect(formatInteger(1284)).toBe(`1${NBSP}284`);
  });

  it("сокращает лайки как в каталоге SourceCraft", () => {
    expect(formatLikes(61.5)).toBe("61,5");
    expect(formatLikes(1600)).toBe(`1,6${NBSP}тыс.`);
    expect(formatLikes(4120)).toBe(`4,1${NBSP}тыс.`);
    expect(formatLikes(2_300_000)).toBe(`2,3${NBSP}млн`);
  });

  it("округляет Score только для вывода", () => {
    expect(formatScore(73.4)).toBe("73");
    expect(formatScore(75.5)).toBe("76");
    expect(formatShare(0.75)).toBe("75%");
  });

  it("показывает объём в двоичных единицах", () => {
    expect(formatBytes(512)).toBe(`512${NBSP}Б`);
    expect(formatBytes(4608)).toBe(`4,5${NBSP}КБ`);
    expect(formatBytes(50 * 1024 * 1024)).toBe(`50${NBSP}МБ`);
  });
});

describe("даты", () => {
  it("пишет дату без «г.»", () => {
    expect(formatDate("2026-09-16T11:05:00Z", "UTC")).toBe("16 сентября 2026");
    expect(formatDateTime("2026-09-16T11:05:00Z", "Europe/Moscow")).toBe("16 сентября 2026, 14:05");
  });

  it("опускает текущий год", () => {
    const now = new Date(2026, 8, 17);
    expect(formatDateTimeCompact("2026-09-15T03:04:00Z", now, "Europe/Moscow")).toBe("15 сентября, 06:04");
    expect(formatDateTimeCompact("2025-12-31T09:00:00Z", now, "UTC")).toBe("31 декабря 2025, 09:00");
  });

  it("говорит о давности по-человечески", () => {
    const now = new Date(2026, 8, 16, 10, 0);
    const daysAgo = (days: number): string => new Date(2026, 8, 16 - days, 23, 30).toISOString();
    expect(formatRelativeDay(daysAgo(0), now)).toBe("сегодня");
    expect(formatRelativeDay(daysAgo(1), now)).toBe("вчера");
    expect(formatRelativeDay(daysAgo(3), now)).toBe("3 дня назад");
    expect(formatRelativeDay(daysAgo(8), now)).toBe("неделю назад");
    expect(formatRelativeDay(daysAgo(21), now)).toBe("3 недели назад");
    expect(formatRelativeDay(daysAgo(45), now)).toBe("месяц назад");
    expect(formatRelativeDay(daysAgo(400), now)).toBe("год назад");
  });

  it("форматирует длительность", () => {
    expect(formatDuration(42)).toBe("42 с");
    expect(formatDuration(134)).toBe("2 мин 14 с");
    expect(formatDuration(180)).toBe("3 мин");
  });
});
