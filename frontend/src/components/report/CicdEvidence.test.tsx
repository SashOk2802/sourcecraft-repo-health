import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ReportCategory } from "../../api/report";
import { CategoryMarks } from "./CategoryMarks";

const historyUrl = "https://sourcecraft.dev/example-org/example-repo/cicd/runs";

function category(reason: string, url: string | null): ReportCategory {
  const unavailable = reason === "cicd_runs_unavailable";
  const summary = "Недостаточно данных для оценки CI/CD.";
  return {
    code: "cicd",
    label: "CI/CD",
    status: unavailable ? "unavailable" : "insufficient_sample",
    score: null,
    weight: 20,
    effectiveWeight: null,
    points: null,
    summary,
    reason,
    evidence: [{
      code: "cicd_data_availability",
      value: unavailable ? "unavailable" : "insufficient",
      normalizedScore: null,
      summary,
      evidence: [{
        source: "sourcecraft-cicd",
        reference: "ci-runs",
        summary: "Ссылка открывает текущую историю SourceCraft; она может отличаться от периода отчёта.",
        url,
      }],
    }],
  };
}

describe("CI/CD evidence in the rendered report", () => {
  it.each([
    "cicd_runs_unavailable",
    "cicd_runs_truncated",
    "cicd_no_runs",
    "cicd_no_automated_runs_in_period",
    "cicd_too_few_outcome_runs",
  ])("renders the source link for %s", (reason) => {
    const html = renderToStaticMarkup(<CategoryMarks categories={[category(reason, historyUrl)]} />);

    expect(html.split(`href="${historyUrl}"`)).toHaveLength(2);
    expect(html).toContain("история запусков CI/CD");
    expect(html).toContain("может отличаться от периода отчёта");
    expect(html).not.toContain("cicd_data_availability");
    expect(html).not.toContain("category-card__score");
  });

  it("does not invent a link for an unsafe repository slug", () => {
    const html = renderToStaticMarkup(<CategoryMarks categories={[category("cicd_runs_unavailable", null)]} />);

    expect(html).not.toContain("href=");
    expect(html).not.toContain("category-card__score");
  });
});
