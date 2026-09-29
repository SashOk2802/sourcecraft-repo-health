import { describe, expect, it } from "vitest";

import { badgePath, formatApiBody, parseRepositorySlug, publicHealthPath } from "./publicApi";

describe("parseRepositorySlug", () => {
  it("понимает org/repo и ссылку на репозиторий SourceCraft", () => {
    expect(parseRepositorySlug("team/platform-api")).toEqual({ organization: "team", repository: "platform-api" });
    expect(parseRepositorySlug(" https://sourcecraft.dev/team/platform-api/ ")).toEqual({
      organization: "team",
      repository: "platform-api",
    });
  });

  it("не принимает то, что не похоже на репозиторий", () => {
    expect(parseRepositorySlug("")).toBeNull();
    expect(parseRepositorySlug("platform-api")).toBeNull();
    expect(parseRepositorySlug("team/platform-api/issues")).toBeNull();
  });
});

describe("адреса публичного API", () => {
  it("строит адреса оценки и бейджа с экранированием", () => {
    const slug = { organization: "team", repository: "project space" };
    expect(publicHealthPath(slug)).toBe("/api/v1/public/repositories/team/project%20space/health");
    expect(badgePath(slug)).toBe("/api/v1/repositories/team/project%20space/badge.svg");
  });

  it("показывает JSON с отступами, а не-JSON как есть", () => {
    expect(formatApiBody('{"score":82.5}')).toBe('{\n  "score": 82.5\n}');
    expect(formatApiBody("Bad Gateway")).toBe("Bad Gateway");
  });
});
