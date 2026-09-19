import { describe, expect, it } from "vitest";

import {
  defaultLeaderboardQuery,
  parseLeaderboardQuery,
  stringifyLeaderboardQuery,
  type LeaderboardSort,
} from "./leaderboard";
import { queryMockLeaderboard } from "./mocks/leaderboard";

describe("фильтры рейтинга в адресной строке", () => {
  it("не пишет значения по умолчанию", () => {
    expect(stringifyLeaderboardQuery(defaultLeaderboardQuery)).toBe("");
  });

  it("переживает круг parse → stringify", () => {
    const query = { language: "C++", sort: "likes" as const, search: "kit", includePreliminary: true, page: 2 };
    expect(parseLeaderboardQuery(stringifyLeaderboardQuery(query))).toEqual(query);
  });

  it("игнорирует мусор", () => {
    expect(parseLeaderboardQuery("?sort=stars&page=-3&language=")).toEqual(defaultLeaderboardQuery);
  });
});

describe("mock-рейтинг", () => {
  const all = (sort: LeaderboardSort) =>
    queryMockLeaderboard({ ...defaultLeaderboardQuery, sort, includePreliminary: true }, 100);

  it("нумерует места подряд и только по Score", () => {
    const { items } = all("score");
    expect(items.map((item) => item.place)).toEqual(items.map((_, index) => index + 1));
    for (let i = 1; i < items.length; i += 1) {
      expect(items[i - 1].score ?? 0).toBeGreaterThanOrEqual(items[i].score ?? 0);
    }
  });

  it("при сортировке по лайкам места не меняются", () => {
    const placeByRepository = new Map(all("score").items.map((item) => [item.repository.id, item.place]));
    const byLikes = all("likes").items;
    expect(byLikes[0].likes ?? 0).toBeGreaterThanOrEqual(byLikes[1].likes ?? 0);
    for (const item of byLikes) {
      expect(item.place).toBe(placeByRepository.get(item.repository.id));
    }
  });

  it("предварительные оценки идут отдельным списком и без мест", () => {
    const response = all("score");
    expect(response.preliminary.length).toBeGreaterThan(0);
    expect(response.preliminary.every((item) => item.place === null)).toBe(true);
    expect(response.items.every((item) => !item.isPreliminary)).toBe(true);
  });

  it("по умолчанию предварительные оценки не показываются", () => {
    const response = queryMockLeaderboard(defaultLeaderboardQuery, 100);
    expect(response.preliminary).toEqual([]);
  });

  it("фильтр по языку не сужает список языков", () => {
    const response = queryMockLeaderboard({ ...defaultLeaderboardQuery, language: "Go" }, 100);
    expect(response.items.every((item) => item.repository.language === "Go")).toBe(true);
    expect(response.languages.length).toBeGreaterThan(1);
  });
});
