import type { Methodology } from "../methodology";
import { mockCategories } from "./catalog";

const texts: Record<(typeof mockCategories)[number]["code"], { measures: string; caveat: string }> = {
  // docs/scoring-methodology.md, §4.2 и docs/security-analyzer.md.
  security: {
    measures: "Результаты AppSec SourceCraft: SAST, SCA и secret scanning, критичность находок и их исправление.",
    caveat:
      "Сами код не сканируем. Пока схема находок AppSec не подтверждена, оценку не ставим — показываем только, есть ли данные. Если скана не было, это «нет данных», а не «уязвимостей нет».",
  },
  // §4.1: доля успешных автоматических прогонов, минимум пять с итогом.
  cicd: {
    measures: "Какая доля автоматических прогонов CI за полгода — на push, merge request и по расписанию — прошла успешно.",
    caveat: "Ручные, отменённые, пропущенные и ещё идущие прогоны не считаем упавшими. Меньше пяти завершённых прогонов — «мало данных».",
  },
  documentation: {
    measures: "README, лицензия, инструкции запуска и тестов, CONTRIBUTING, CODEOWNERS.",
    caveat: "Качество текста не оцениваем по длине, а сам файл ещё не значит хорошую документацию.",
  },
  // docs/scoring-methodology.md, §3: частоту коммитов v1 не измеряет — у SourceCraft нет API коммитов.
  activity: {
    measures: "Когда проект меняли в последний раз, сколько merge requests смержили и релизов выпустили за полгода, сколько участников.",
    caveat: "Объём ограничен сверху: три смерженных MR дают столько же, сколько семьдесят. Частоту коммитов в v1 не считаем.",
  },
  // docs/scoring-methodology.md, §2.
  issues: {
    measures: "Доля задач без движения дольше 90 дней, успевают ли разбирать новые и сколько дней уходит на решение.",
    caveat: "За само число открытых задач не штрафуем, а отменённые задачи решёнными не считаем.",
  },
  code_health: {
    measures: "TODO и FIXME: сколько их, где они и насколько старые.",
    caveat: "Слабый сигнал, поэтому и вес небольшой. Сгенерированный и сторонний код не считаем.",
  },
};

export const mockMethodology: Methodology = {
  version: "v1",
  categories: mockCategories.map((category) => ({ ...category, ...texts[category.code] })),
  criticalScoreLimit: 60,
  schedule: { regularHours: 24, activeHours: 6, inactiveHours: 72 },
};
