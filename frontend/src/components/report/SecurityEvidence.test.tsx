import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ReportCategory } from "../../api/report";
import { CategoryMarks } from "./CategoryMarks";

const overviewUrl = "https://sourcecraft.dev/example-org/example-repo/security/overview";

function category(status: "unavailable" | "insufficient_sample" | "error", url: string | null): ReportCategory {
  const summary = "Недостаточно данных для оценки безопасности.";
  return {
    code: "security",
    label: "Безопасность",
    status,
    score: null,
    weight: 25,
    effectiveWeight: null,
    points: null,
    summary,
    reason: "appsec_coverage_not_confirmed",
    evidence: [{
      code: "appsec_data_availability",
      value: status,
      normalizedScore: null,
      summary,
      evidence: [{
        source: "sourcecraft-appsec",
        reference: "appsec-defects",
        summary: "Ссылка открывает текущий обзор SourceCraft, который может отличаться от снимка отчёта.",
        url,
      }],
    }],
  };
}

describe("Security evidence in the rendered report", () => {
  it.each(["unavailable", "insufficient_sample", "error"] as const)(
    "renders the source link when Security is %s",
    (status) => {
      const html = renderToStaticMarkup(<CategoryMarks categories={[category(status, overviewUrl)]} />);

      expect(html.match(new RegExp(`href="${overviewUrl}"`, "g"))).toHaveLength(1);
      expect(html).toContain("обзор безопасности");
      expect(html).toContain("может отличаться от снимка отчёта");
      expect(html).not.toContain("appsec_data_availability");
      expect(html).not.toContain("category-card__score");
    },
  );

  it("does not invent an address when the backend omitted it", () => {
    const html = renderToStaticMarkup(<CategoryMarks categories={[category("unavailable", null)]} />);

    expect(html).not.toContain("href=");
    expect(html).not.toContain("category-card__score");
  });
});
