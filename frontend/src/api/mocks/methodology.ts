import type { MethodologyPayload } from "../methodology";
import { mockCategories } from "./catalog";

/** Ответ GET /api/v1/methodology в том виде, в каком его отдаёт backend на main. */
export const mockMethodologyPayload: MethodologyPayload = {
  version: "v1",
  categories: mockCategories.map(({ code, label, weight }) => ({ code, label, weight })),
  scoreLimits: [
    {
      code: "security-open-critical",
      maximumScore: 60,
      summary: "Подтверждённая открытая критическая AppSec-уязвимость ограничивает Score.",
    },
  ],
};
