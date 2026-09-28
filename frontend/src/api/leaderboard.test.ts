import { describe, expect, it } from "vitest";

import {
  defaultLeaderboardQuery,
  lastLeaderboardPage,
  parseLeaderboardQuery,
  patchLeaderboardSearch,
  stringifyLeaderboardQuery,
  toLeaderboardResponse,
  type LeaderboardItem,
  type LeaderboardSort,
} from "./leaderboard";
import { queryMockLeaderboard, rankByScore } from "./mocks/leaderboard";

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

describe("смена фильтра", () => {
  it("не откатывает фильтр, выбранный, пока ждал отложенный поиск", () => {
    // Начали печатать «kit», за 300 мс до отправки выбрали язык Go — поиск применяется поверх Go.
    expect(patchLeaderboardSearch("?language=Go", { search: "kit" })).toBe("?language=Go&q=kit");
  });

  it("возвращает на первую страницу, если страницу не задали явно", () => {
    expect(patchLeaderboardSearch("?sort=likes&page=3", { language: "Rust" })).toBe("?language=Rust&sort=likes");
    expect(patchLeaderboardSearch("?page=3", { page: 2 })).toBe("?page=2");
  });

  it("сброс фильтров очищает адрес", () => {
    expect(patchLeaderboardSearch("?language=Go&q=kit&page=2", defaultLeaderboardQuery)).toBe("");
  });
});

describe("последняя страница", () => {
  it("считает страницы по total", () => {
    expect(lastLeaderboardPage(17, 15)).toBe(2);
    expect(lastLeaderboardPage(30, 15)).toBe(2);
    expect(lastLeaderboardPage(31, 15)).toBe(3);
  });

  it("у пустого списка последняя страница — первая", () => {
    expect(lastLeaderboardPage(0, 15)).toBe(1);
  });

  it("?page=999 за концом списка — это страница за пределами", () => {
    const response = queryMockLeaderboard({ ...defaultLeaderboardQuery, page: 999 }, 15);
    expect(response.items).toEqual([]);
    expect(999).toBeGreaterThan(lastLeaderboardPage(response.total, response.pageSize));
  });
});

describe("mock-рейтинг", () => {
  const all = (sort: LeaderboardSort) =>
    queryMockLeaderboard({ ...defaultLeaderboardQuery, sort, includePreliminary: true }, 100);

  it("нумерует места только по Score", () => {
    const { items } = all("score");
    expect(items[0].place).toBe(1);
    for (let i = 1; i < items.length; i += 1) {
      expect(items[i - 1].score ?? 0).toBeGreaterThanOrEqual(items[i].score ?? 0);
      expect(items[i - 1].place ?? 0).toBeLessThanOrEqual(items[i].place ?? 0);
    }
  });

  it("равный Score делит место, как в backend/app/leaderboard/policy.py", () => {
    const row = (id: string, score: number) => ({ repository: { id }, score, place: null }) as unknown as LeaderboardItem;
    const items = [row("c", 80), row("a", 90), row("b", 80), row("d", 70)];
    rankByScore(items);
    expect(items.map((item) => [item.repository.id, item.place])).toEqual([
      ["c", 2],
      ["a", 1],
      ["b", 2],
      ["d", 4],
    ]);
  });

  it("ищет по организации и репозиторию, а не по описанию", () => {
    const byName = queryMockLeaderboard({ ...defaultLeaderboardQuery, search: "kvant-lab/sched" }, 100);
    expect(byName.items.map((item) => item.repository.name)).toEqual(["kvant-lab/scheduler"]);
    // «Планировщик задач…» — описание kvant-lab/scheduler: по нему backend не ищет.
    const byDescription = queryMockLeaderboard({ ...defaultLeaderboardQuery, search: "планировщик" }, 100);
    expect(byDescription.items).toEqual([]);
  });

  it("считает предварительные, даже когда их список не запрошен", () => {
    const response = queryMockLeaderboard(defaultLeaderboardQuery, 100);
    expect(response.preliminaryTotal).toBeGreaterThan(0);
    expect(response.methodologyVersion).toBe("v2");
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

describe("ответ backend о рейтинге", () => {
  it("принимает имена из backend/app/leaderboard/policy.py", () => {
    const response = toLeaderboardResponse(
      {
        methodologyVersion: "v1",
        entries: [
          {
            repositoryId: "repo-1",
            organizationSlug: "team",
            repositorySlug: "api",
            score: 91.5,
            isPreliminary: false,
            language: "Go",
            likes: 12,
            lastActivityAt: "2026-09-20T10:00:00Z",
            rank: 1,
          },
        ],
        total: 1,
        preliminaryEntries: [],
        preliminaryTotal: 3,
      },
      defaultLeaderboardQuery,
    );
    const [item] = response.items;
    expect(item.place).toBe(1);
    expect(item.repository).toMatchObject({ id: "repo-1", name: "team/api", language: "Go", url: null });
    // Без снимка анализа строку не к чему вести — страница не делает её ссылкой.
    expect(item.analysisId).toBeNull();
    expect(item.categories).toEqual([]);
    expect(response.preliminaryTotal).toBe(3);
    expect(response.methodologyVersion).toBe("v1");
    expect(response.languages).toEqual([{ name: "Go", count: 1 }]);
    expect(response.pageSize).toBeGreaterThan(0);
  });

  it("принимает и формат из предложения к контракту", () => {
    const mock = queryMockLeaderboard({ ...defaultLeaderboardQuery, includePreliminary: true }, 15);
    expect(toLeaderboardResponse(mock, defaultLeaderboardQuery)).toEqual(mock);
  });

  it("пустой ответ не ломает страницу", () => {
    const response = toLeaderboardResponse({}, defaultLeaderboardQuery);
    expect(response.items).toEqual([]);
    expect(response.preliminary).toEqual([]);
    expect(response.total).toBe(0);
    expect(response.methodologyVersion).toBeNull();
  });
});
