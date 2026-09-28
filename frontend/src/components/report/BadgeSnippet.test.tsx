import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { RepositoryReport } from "../../api/report";
import { BadgeSnippet, buildBadgeSnippets } from "./BadgeSnippet";

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

  it("builds correct badge URLs and markdown/HTML snippets with baseUrl", () => {
    const snippets = buildBadgeSnippets(sampleReport, "https://repo-health.example.com");

    expect(snippets.repoBadgeUrl).toBe(
      "https://repo-health.example.com/api/v1/repositories/test-org/test-repo/badge.svg",
    );
    expect(snippets.analysisPageUrl).toBe(
      "https://repo-health.example.com/analyses/analysis-99",
    );
    expect(snippets.markdownSnippet).toBe(
      "[![Repo Health](https://repo-health.example.com/api/v1/repositories/test-org/test-repo/badge.svg)](https://repo-health.example.com/analyses/analysis-99)",
    );
    expect(snippets.htmlSnippet).toBe(
      '<a href="https://repo-health.example.com/analyses/analysis-99"><img src="https://repo-health.example.com/api/v1/repositories/test-org/test-repo/badge.svg" alt="Repo Health"></a>',
    );
  });

  it("properly URI-encodes special characters in slugs", () => {
    const specialReport: RepositoryReport = {
      ...sampleReport,
      repository: {
        ...sampleReport.repository,
        organizationSlug: "team/alpha",
        repositorySlug: "project space",
      },
    };

    const snippets = buildBadgeSnippets(specialReport, "https://example.com");

    expect(snippets.repoBadgeUrl).toBe(
      "https://example.com/api/v1/repositories/team%2Falpha/project%20space/badge.svg",
    );
  });

});
