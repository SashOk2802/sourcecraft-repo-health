import type { CategoryStatus } from "../common";
import { daysAgo } from "./time";

/*
 * Вымышленные репозитории для разработки интерфейса.
 * Названия придуманы, чтобы условные оценки не приписывались настоящим проектам SourceCraft.
 */

/** Категории и веса методики v1 (docs/api-contract.md): вес в процентах. */
export const mockCategories = [
  { code: "security", label: "Безопасность", weight: 25 },
  { code: "cicd", label: "CI/CD", weight: 20 },
  { code: "documentation", label: "Документация", weight: 20 },
  { code: "activity", label: "Активность", weight: 15 },
  { code: "issues", label: "Работа с issues", weight: 15 },
  { code: "code_health", label: "Состояние кода", weight: 5 },
] as const;

export type MockCategoryCode = (typeof mockCategories)[number]["code"];

/** Число — измеренная оценка, строка — статус, при котором оценки нет. */
export type MockCategoryValue = number | Exclude<CategoryStatus, "measured">;

export interface MockRepository {
  id: string;
  organizationSlug: string;
  repositorySlug: string;
  description: string;
  language: string;
  likes: number;
  lastActivityAt: string;
  visibility: "public" | "private";
  /** null — репозиторий ещё ни разу не анализировался. */
  categories: Record<MockCategoryCode, MockCategoryValue> | null;
  /** Ограничение Score из-за подтверждённой критической проблемы. */
  scoreLimit?: number;
  /** Отчёт появится только после первого запуска анализа пользователем. */
  awaitingFirstAnalysis?: boolean;
  /** Прошлый плановый анализ не удался. */
  lastAnalysisFailed?: boolean;
}

export const mockRepositories: MockRepository[] = [
  {
    id: "repo-1001",
    organizationSlug: "kvant-lab",
    repositorySlug: "scheduler",
    description: "Планировщик задач с распределёнными блокировками поверх PostgreSQL",
    language: "Go",
    likes: 1840,
    lastActivityAt: daysAgo(1),
    visibility: "public",
    categories: { security: 95, cicd: 93, documentation: 90, activity: 94, issues: 88, code_health: 84 },
  },
  {
    id: "repo-1002",
    organizationSlug: "severny",
    repositorySlug: "geo-tiles",
    description: "Сервер векторных тайлов для веб-карт",
    language: "Rust",
    likes: 2310,
    lastActivityAt: daysAgo(2),
    visibility: "public",
    categories: { security: 90, cicd: 96, documentation: 86, activity: 88, issues: 91, code_health: 79 },
  },
  {
    id: "repo-1003",
    organizationSlug: "tundra-ui",
    repositorySlug: "kit",
    description: "React-компоненты и дизайн-токены для внутренних сервисов",
    language: "TypeScript",
    likes: 3620,
    lastActivityAt: daysAgo(0),
    visibility: "public",
    categories: { security: 88, cicd: 90, documentation: 95, activity: 97, issues: 72, code_health: 70 },
  },
  {
    id: "repo-1004",
    organizationSlug: "pixelfarm",
    repositorySlug: "imgproxy-lite",
    description: "Лёгкий прокси для ресайза и кэширования изображений",
    language: "Go",
    likes: 1120,
    lastActivityAt: daysAgo(6),
    visibility: "public",
    categories: { security: 74, cicd: 95, documentation: 83, activity: 77, issues: 90, code_health: 86 },
  },
  {
    id: "repo-1005",
    organizationSlug: "klyuch",
    repositorySlug: "secrets-operator",
    description: "Kubernetes-оператор для ротации секретов",
    language: "Go",
    likes: 960,
    lastActivityAt: daysAgo(3),
    visibility: "public",
    categories: { security: 97, cicd: 85, documentation: 74, activity: 80, issues: 83, code_health: 90 },
  },
  {
    id: "repo-1006",
    organizationSlug: "edu-kit",
    repositorySlug: "olympiad-judge",
    description: "Проверяющая система для школьных олимпиад по информатике",
    language: "C++",
    likes: 412,
    lastActivityAt: daysAgo(5),
    visibility: "public",
    categories: {
      security: 84,
      cicd: "error",
      documentation: 80,
      activity: "insufficient_sample",
      issues: "not_applicable",
      code_health: 66,
    },
  },
  {
    id: "repo-1007",
    organizationSlug: "vectorhub",
    repositorySlug: "embed-search",
    description: "Семантический поиск по эмбеддингам поверх PostgreSQL",
    language: "Python",
    likes: 2750,
    lastActivityAt: daysAgo(1),
    visibility: "public",
    categories: { security: 70, cicd: 82, documentation: 81, activity: 95, issues: 64, code_health: 58 },
  },
  {
    id: "repo-1008",
    organizationSlug: "gorod-dev",
    repositorySlug: "transit-api",
    description: "API расписаний городского транспорта",
    language: "TypeScript",
    likes: 1290,
    lastActivityAt: daysAgo(2),
    visibility: "public",
    categories: { security: "unavailable", cicd: 58, documentation: 85, activity: 91, issues: 58, code_health: 70 },
  },
  {
    id: "repo-1009",
    organizationSlug: "polar-io",
    repositorySlug: "logship",
    description: "Агент доставки логов с буфером на диске",
    language: "Rust",
    likes: 640,
    lastActivityAt: daysAgo(9),
    visibility: "public",
    categories: { security: 85, cicd: 77, documentation: 69, activity: 71, issues: 80, code_health: 88 },
  },
  {
    id: "repo-1010",
    organizationSlug: "cyrillic",
    repositorySlug: "hyphenator",
    description: "Переносы и неразрывные пробелы для русского текста",
    language: "JavaScript",
    likes: 880,
    lastActivityAt: daysAgo(70),
    visibility: "public",
    categories: { security: 92, cicd: 88, documentation: 90, activity: 38, issues: 76, code_health: 94 },
  },
  {
    id: "repo-1011",
    organizationSlug: "swiftly",
    repositorySlug: "maps-kit",
    description: "Компоненты карт для iOS-приложений",
    language: "Swift",
    likes: 540,
    lastActivityAt: daysAgo(20),
    visibility: "public",
    categories: { security: 80, cicd: 71, documentation: 77, activity: 62, issues: 68, code_health: 73 },
  },
  {
    id: "repo-1012",
    organizationSlug: "lastochka",
    repositorySlug: "android-sdk",
    description: "SDK мобильных платежей для Android",
    language: "Kotlin",
    likes: 1530,
    lastActivityAt: daysAgo(4),
    visibility: "public",
    categories: { security: 22, cicd: 91, documentation: 85, activity: 86, issues: 79, code_health: 81 },
    scoreLimit: 60,
  },
  {
    id: "repo-1013",
    organizationSlug: "obmen",
    repositorySlug: "http-services",
    description: "HTTP-сервисы обмена данными для 1С:Предприятия",
    language: "1C Enterprise",
    likes: 205,
    lastActivityAt: daysAgo(6),
    visibility: "public",
    categories: { security: "unavailable", cicd: 15, documentation: 72, activity: 83, issues: 69, code_health: 74 },
  },
  {
    id: "repo-1014",
    organizationSlug: "astra-data",
    repositorySlug: "etl-kit",
    description: "Набор ETL-пайплайнов на Airflow",
    language: "Python",
    likes: 318,
    lastActivityAt: daysAgo(12),
    visibility: "public",
    categories: { security: 76, cicd: 64, documentation: 58, activity: 67, issues: 70, code_health: 61 },
  },
  {
    id: "repo-1015",
    organizationSlug: "haskell-ru",
    repositorySlug: "parsers-course",
    description: "Курс по парсер-комбинаторам с задачами и автопроверкой",
    language: "Haskell",
    likes: 230,
    lastActivityAt: daysAgo(200),
    visibility: "public",
    categories: { security: "unavailable", cicd: 66, documentation: 92, activity: 20, issues: 55, code_health: 85 },
  },
  {
    id: "repo-1016",
    organizationSlug: "landing-kit",
    repositorySlug: "static-site",
    description: "Шаблон статического сайта с деплоем в Object Storage",
    language: "HTML",
    likes: 35,
    lastActivityAt: daysAgo(45),
    visibility: "public",
    categories: {
      security: "not_applicable",
      cicd: 72,
      documentation: 61,
      activity: 33,
      issues: "not_applicable",
      code_health: 90,
    },
  },
  {
    id: "repo-1017",
    organizationSlug: "meteo-lab",
    repositorySlug: "nowcast",
    description: "Краткосрочный прогноз осадков по радарным снимкам",
    language: "Jupyter Notebook",
    likes: 470,
    lastActivityAt: daysAgo(15),
    visibility: "public",
    categories: {
      security: "unavailable",
      cicd: 20,
      documentation: 63,
      activity: 58,
      issues: "not_applicable",
      code_health: 40,
    },
  },
  {
    id: "repo-1018",
    organizationSlug: "infra-snippets",
    repositorySlug: "ansible-roles",
    description: "Роли Ansible для типовых серверов",
    language: "Shell",
    likes: 410,
    lastActivityAt: daysAgo(30),
    visibility: "public",
    categories: { security: 58, cicd: 44, documentation: 66, activity: 51, issues: 47, code_health: 70 },
  },
  {
    id: "repo-1019",
    organizationSlug: "teplo",
    repositorySlug: "thermostat-firmware",
    description: "Прошивка умного термостата на ESP32",
    language: "C",
    likes: 150,
    lastActivityAt: daysAgo(40),
    visibility: "public",
    categories: { security: 61, cicd: 48, documentation: 55, activity: 42, issues: 50, code_health: 63 },
  },
  {
    id: "repo-1020",
    organizationSlug: "shkola-21",
    repositorySlug: "diary",
    description: "Электронный дневник для небольшой школы",
    language: "Java",
    likes: 96,
    lastActivityAt: daysAgo(120),
    visibility: "public",
    categories: { security: 45, cicd: 30, documentation: 41, activity: 25, issues: 35, code_health: 52 },
  },
  {
    id: "repo-1021",
    organizationSlug: "empty-org",
    repositorySlug: "new-service",
    description: "Заготовка нового сервиса",
    language: "Go",
    likes: 3,
    lastActivityAt: daysAgo(1),
    visibility: "public",
    categories: {
      security: "unavailable",
      cicd: "error",
      documentation: "error",
      activity: "insufficient_sample",
      issues: "unavailable",
      code_health: "error",
    },
  },
  {
    id: "repo-1022",
    organizationSlug: "nalog-bot",
    repositorySlug: "receipts",
    description: "Telegram-бот для учёта чеков",
    language: "Python",
    likes: 60,
    lastActivityAt: daysAgo(2),
    visibility: "public",
    categories: null,
  },

  // Организация из каталога демо-кабинета (src/api/mocks/me.ts): её репозитории можно проверить после входа.
  {
    id: "repo-2001",
    organizationSlug: "shkola-it",
    repositorySlug: "homework-checker",
    description: "Автопроверка домашних заданий по информатике",
    language: "Python",
    likes: 0,
    lastActivityAt: daysAgo(1),
    visibility: "public",
    categories: { security: 81, cicd: 64, documentation: 52, activity: 88, issues: "not_applicable", code_health: 60 },
  },
  {
    id: "repo-2002",
    organizationSlug: "shkola-it",
    repositorySlug: "lesson-bot",
    description: "Бот, который присылает ученикам материалы урока",
    language: "TypeScript",
    likes: 0,
    lastActivityAt: daysAgo(3),
    visibility: "public",
    categories: { security: 90, cicd: 72, documentation: 45, activity: 79, issues: "not_applicable", code_health: 83 },
    awaitingFirstAnalysis: true,
  },
  {
    id: "repo-2003",
    organizationSlug: "shkola-it",
    repositorySlug: "olympiad-site",
    description: "Сайт школьной олимпиады",
    language: "HTML",
    likes: 12,
    lastActivityAt: daysAgo(9),
    visibility: "public",
    categories: {
      security: "not_applicable",
      cicd: 70,
      documentation: 58,
      activity: 64,
      issues: "not_applicable",
      code_health: 91,
    },
    lastAnalysisFailed: true,
  },
];

/** Идентификатор снимка анализа в mock-данных: по нему открывается отчёт. */
export function mockAnalysisId(repository: MockRepository): string {
  return `an-${repository.id.replace("repo-", "")}`;
}

export function findMockRepository(organizationSlug: string, repositorySlug: string): MockRepository | undefined {
  return mockRepositories.find(
    (repository) =>
      repository.organizationSlug === organizationSlug && repository.repositorySlug === repositorySlug,
  );
}

export function findMockRepositoryByAnalysisId(analysisId: string): MockRepository | undefined {
  return mockRepositories.find((repository) => mockAnalysisId(repository) === analysisId);
}
