import type { Methodology } from "../methodology";
import { mockCategories } from "./catalog";

const texts: Record<(typeof mockCategories)[number]["code"], { measures: string; caveat: string }> = {
  security: {
    measures: "Результаты AppSec SourceCraft: SAST, SCA и secret scanning, критичность находок и их исправление.",
    caveat: "Сами код не сканируем. Если скана не было, это «нет данных», а не «уязвимостей нет».",
  },
  cicd: {
    measures: "Есть ли CI, чем заканчиваются последние прогоны, повторяются ли одни и те же сбои.",
    caveat: "Отменённый или ещё идущий прогон не считаем упавшим.",
  },
  documentation: {
    measures: "README, лицензия, инструкции запуска и тестов, CONTRIBUTING, CODEOWNERS.",
    caveat: "Качество текста не оцениваем по длине, а сам файл ещё не значит хорошую документацию.",
  },
  activity: {
    measures: "Давность изменений, недели с коммитами, участники, merge requests и релизы.",
    caveat: "Поток мелких коммитов не поднимает оценку бесконечно, а зрелый проект может меняться редко.",
  },
  issues: {
    measures: "Зависшие задачи, их возраст и то, как быстро разбирают новые.",
    caveat: "За само число открытых задач не штрафуем.",
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
