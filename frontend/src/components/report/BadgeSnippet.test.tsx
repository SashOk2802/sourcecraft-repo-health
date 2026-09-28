import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { RepositoryReport } from "../../api/report";
import { BadgeSnippet } from "./BadgeSnippet";

const sampleReport: RepositoryReport = {
  repository: {
    id: "repo-42",
    organizationSlug: "test-org",
    repositorySlug: "test-repo",
    name: "test-org/test-repo",
    url: "https://sourcecraft.dev/test-org/test-repo",
  },
  analysis: {
    id: "analysis-99",
    status: "completed",
    analyzedAt: "2026-09-28T12:00:00Z",
    commitSha: "abc1234",
    methodologyVersion: "1.0.0",
    coverage: 1.0,
    isPreliminary: false,
    scoreLimit: null,
  },
  score: 85,
  scoreDetails: {
    measuredWeight: 1.0,
    applicableWeight: 1.0,
  },
  categories: [],
  recommendations: [],
};

describe("BadgeSnippet", () => {
  it("renders badge action button", () => {
    const html = renderToStaticMarkup(<BadgeSnippet report={sampleReport} />);

    expect(html).toContain("Бейдж для README");
  });
});
