import type { MethodologyPayload } from "../methodology";
import { mockCategories } from "./catalog";

/** Ответ GET /api/v1/methodology в том виде, в каком его отдаёт backend на main (backend/app/scoring/methodology.py). */
export const mockMethodologyPayload: MethodologyPayload = {
  version: "v2",
  categories: mockCategories.map(({ code, label, weight }) => ({ code, label, weight })),
  scoreLimits: [
    {
      code: "security-open-critical",
      maximumScore: 60,
      summary: "Подтверждённая открытая критическая AppSec-уязвимость ограничивает Score.",
    },
  ],
  security: {
    formula: "100 - min(100, sum(penaltyPerFinding * min(openFindings, maximumFindings)))",
    summary: "Security Score учитывает только открытые findings из полных обезличенных результатов SAST, SCA и secret scanning.",
    severityPenalties: [
      { severity: "CRITICAL", penaltyPerFinding: 60, maximumFindings: 2 },
      { severity: "HIGH", penaltyPerFinding: 15, maximumFindings: 3 },
      { severity: "MEDIUM", penaltyPerFinding: 5, maximumFindings: 4 },
      { severity: "LOW", penaltyPerFinding: 1, maximumFindings: 10 },
      { severity: "INFO", penaltyPerFinding: 0, maximumFindings: 0 },
    ],
    eligibility:
      "Все три движка должны вернуть полный результат с известными severity и status; иначе категория имеет статус insufficient_sample и не участвует в Score.",
  },
};
