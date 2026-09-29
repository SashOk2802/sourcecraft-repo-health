import { Label, Text } from "@gravity-ui/uikit";
import type { ReactNode } from "react";

import type { CategoryStatus, RecommendationPriority } from "../api/common";
import { fetchMethodology, type Methodology, type MethodologyCategory } from "../api/methodology";
import { ErrorNote, LoadingNote } from "../components/PageNotes";
import { ScoreBar } from "../components/ScoreBar";
import { dataOf, useAsync } from "../hooks/useAsync";
import { useHashScroll } from "../hooks/useHashScroll";
import { usePageMeta } from "../hooks/usePageMeta";
import { formatPoints, formatScore, plural } from "../lib/format";
import { categoryStatusLabels, priorityLabels } from "../lib/labels";
import { Link } from "../router";
import { paths } from "../routes";
import "./MethodologyPage.css";

// Условные оценки для разбора формулы на примере.
const exampleScores: Record<string, number> = {
  security: 80,
  cicd: 90,
  documentation: 70,
  activity: 60,
  issues: 80,
  code_health: 50,
};

const sections = [
  { id: "categories", title: "Шесть категорий" },
  { id: "formula", title: "Как получается итог" },
  { id: "no-data", title: "Когда данных нет" },
  { id: "critical", title: "Критические проблемы" },
  { id: "recommendations", title: "Рекомендации" },
  { id: "not-counted", title: "Что не влияет на оценку" },
  { id: "schedule", title: "Как часто пересчитываем" },
] as const;

export function MethodologyPage() {
  usePageMeta({
    title: "Как считаем",
    description:
      "Как считается Repo Health Score: шесть категорий и их веса, что происходит без данных и почему критическая уязвимость ограничивает оценку.",
  });
  const [state, reload] = useAsync(fetchMethodology, []);
  const methodology = dataOf(state);
  // Разделы появляются после загрузки методики: ссылка вида /methodology#no-data ждёт их.
  useHashScroll(Boolean(methodology));

  return (
    <div className="page__inner methodology">
      <div className="page__header">
        <div>
          <Text variant="header-2" as="h1" className="page__title">
            Как считаем Repo Health Score
          </Text>
          <Text variant="body-2" color="secondary" className="page__lead">
            Оценка от 0 до 100 складывается из шести категорий. Каждая опирается на данные SourceCraft: историю Git,
            CI, issues, merge requests и результаты AppSec. Ниже — что именно мы смотрим, как из этого получается
            одно число и что происходит, когда данных нет.
          </Text>
        </div>
        {methodology && <Label size="m">Методика {methodology.version}</Label>}
      </div>

      <nav className="methodology__toc" aria-label="Разделы методики">
        {sections.map((section, index) => (
          <a key={section.id} href={`#${section.id}`}>
            <span className="num">{index + 1}</span> {section.title}
          </a>
        ))}
      </nav>

      {!methodology && state.status === "loading" && <LoadingNote>Загружаем методику</LoadingNote>}
      {!methodology && state.status === "error" && (
        <ErrorNote title="Не удалось загрузить методику" error={state.error} onRetry={reload} />
      )}
      {methodology && <MethodologyBody methodology={methodology} />}
    </div>
  );
}

function MethodologyBody({ methodology }: { methodology: Methodology }) {
  const { categories } = methodology;
  const weakest = [...categories].sort((a, b) => a.weight - b.weight)[0];

  return (
    <>
      <Section index={0}>
        <table className="methodology__table">
          <thead>
            <tr>
              <th scope="col">Категория</th>
              <th scope="col" className="methodology__weight">
                Вес
              </th>
              <th scope="col">Что учитываем</th>
              <th scope="col">Чего не делаем</th>
            </tr>
          </thead>
          <tbody>
            {categories.map((category) => (
              <tr key={category.code}>
                <th scope="row">{category.label}</th>
                <td className="methodology__weight num">{formatPoints(category.weight)}%</td>
                <td className="methodology__measures">{category.measures}</td>
                <td className="methodology__caveat">{category.caveat}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {weakest && (
          <Text variant="body-1" color="secondary">
            Небольшой вес категории «{weakest.label}» — осознанный: этот сигнал слабо говорит о качестве проекта и не
            должен определять итог.
          </Text>
        )}
      </Section>

      <Section index={1}>
        <Text variant="body-2">
          Сначала каждая категория получает оценку от 0 до 100 по своим метрикам. Затем считаем среднее, взвешенное
          по весам категорий, у которых есть данные:
        </Text>
        <p className="methodology__formula num">Score = Σ (оценка × вес) ÷ Σ весов категорий с данными</p>
        <FormulaExample categories={categories} />
      </Section>

      <Section index={2}>
        <Text variant="body-2">
          <b>Нет данных — не ноль баллов.</b> Если источник не ответил или скан AppSec не запускался, категория не
          участвует в расчёте, а веса остальных пропорционально растягиваются. В таблице категорий у неё стоит
          «не участвует в расчёте», а рядом с оценкой — метка:
        </Text>
        <div className="row-gap-8">
          <Label theme="warning">Предварительная оценка</Label>
        </div>
        <NoDataExample categories={categories} />
        <ul className="methodology__statuses">
          {statusExamples.map((example) => (
            <li key={example.status}>
              <span className="methodology__status-bar">
                <ScoreBar score={null} status={example.status} label="Категория" size="s" />
              </span>
              <Text variant="body-2">{categoryStatusLabels[example.status]}</Text>
              <Text variant="body-1" color="secondary">
                {example.text}
              </Text>
            </li>
          ))}
        </ul>
      </Section>

      <Section index={3}>
        {methodology.criticalScoreLimit !== null ? (
          <>
            <Text variant="body-2">
              Открытая критическая уязвимость не должна теряться за хорошей документацией и живым CI. Пока AppSec
              подтверждает такую проблему, Score не поднимается выше {methodology.criticalScoreLimit}, а рядом с
              оценкой видно, каким он был бы без ограничения.
            </Text>
            <div className="row-gap-8">
              <Label theme="danger">Ограничено: не выше {methodology.criticalScoreLimit}</Label>
            </div>
            <Text variant="body-1" color="secondary">
              Ограничение не применяется, если данных AppSec нет, источник недоступен, находка признана ложной или
              уязвимость уже исправлена. Критичная находка, которую ещё не подтвердили, снижает оценку безопасности,
              но общий Score не ограничивает.
            </Text>
          </>
        ) : (
          <Text variant="body-2">В этой версии методики отдельного ограничения за критические проблемы нет.</Text>
        )}
      </Section>

      <Section index={4}>
        <Text variant="body-2">
          Каждая рекомендация опирается на факты — прогоны CI, issues, файлы, находки AppSec — и говорит, что
          изменится, если её выполнить. Порядок — по приоритету:
        </Text>
        <ul className="methodology__priorities">
          {priorityExamples.map((example) => (
            <li key={example.priority}>
              <Label theme={priorityThemes[example.priority]} size="s">
                {priorityLabels[example.priority]}
              </Label>
              <Text variant="body-1" color="secondary">
                {example.text}
              </Text>
            </li>
          ))}
        </ul>
        <Text variant="body-1" color="secondary">
          Возможные приросты не суммируются: рекомендации могут влиять на одни и те же метрики или снять общее
          ограничение Score.
        </Text>
      </Section>

      <Section index={5}>
        <Text variant="body-2">
          <b>Лайки.</b> По ним можно отсортировать рейтинг, но место всегда определяет Score — рейтинг не
          превращается в список популярности.
        </Text>
        <Text variant="body-2">
          <b>Объём ради объёма.</b> Сотня пустых коммитов не делает проект активнее, а длинный README — понятнее.
          Метрики ограничены сверху, чтобы их нельзя было просто накрутить.
        </Text>
      </Section>

      <Section index={6}>
        {methodology.schedule && (
          <Text variant="body-2">
            Открытые репозитории пересчитываются по расписанию: обычно раз в{" "}
            {hoursText(methodology.schedule.regularHours)}, активные — с изменениями за последние{" "}
            {daysText(methodology.schedule.activeWithinDays)} — раз в {hoursText(methodology.schedule.activeHours)},
            а без изменений больше {daysText(methodology.schedule.inactiveAfterDays)} — раз в{" "}
            {hoursText(methodology.schedule.inactiveHours)}. Если SourceCraft временно не ответил, повтор идёт
            раньше: через 15 минут, потом всё реже, но не реже раза в 6 часов.
          </Text>
        )}
        <Text variant="body-2">
          Свой репозиторий можно проверить в любой момент в разделе{" "}
          <Link to={paths.myRepositories()}>«Мои репозитории»</Link>.
        </Text>
        <Text variant="body-1" color="secondary">
          Время анализа указано в каждом отчёте: он строится на неизменяемом снимке, и ссылка всегда показывает один
          и тот же результат.
        </Text>
      </Section>
    </>
  );
}

function Section({ index, children }: { index: number; children: ReactNode }) {
  const section = sections[index];
  return (
    <section id={section.id} className="section methodology__section">
      <Text variant="body-1" color="secondary" className="methodology__number num">
        {String(index + 1).padStart(2, "0")}
      </Text>
      <div className="methodology__body">
        <Text variant="subheader-2" as="h2" className="methodology__heading">
          {section.title}
        </Text>
        {children}
      </div>
    </section>
  );
}

function FormulaExample({ categories }: { categories: MethodologyCategory[] }) {
  const rows = categories.filter((category) => exampleScores[category.code] !== undefined);
  if (rows.length === 0) return null;

  const total = rows.reduce((sum, category) => sum + (exampleScores[category.code] * category.weight) / 100, 0);

  return (
    <table className="methodology__example">
      <caption>Пример с условными оценками</caption>
      <thead>
        <tr>
          <th scope="col">Категория</th>
          <th scope="col">Оценка</th>
          <th scope="col">Вес</th>
          <th scope="col">Вклад в Score</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((category) => (
          <tr key={category.code}>
            <th scope="row">{category.label}</th>
            <td className="num">{exampleScores[category.code]}</td>
            <td className="num">× {formatPoints(category.weight)}%</td>
            <td className="num">{formatPoints((exampleScores[category.code] * category.weight) / 100)}</td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr>
          <th scope="row">Итого</th>
          <td />
          <td />
          <td className="num">
            {formatPoints(total)} → <b>{formatScore(total)}</b>
          </td>
        </tr>
      </tfoot>
    </table>
  );
}

function NoDataExample({ categories }: { categories: MethodologyCategory[] }) {
  const missing = categories.find((category) => category.code === "security") ?? categories[0];
  const rows = categories.filter(
    (category) => category.code !== missing.code && exampleScores[category.code] !== undefined,
  );
  if (!missing || rows.length === 0) return null;

  const weightSum = rows.reduce((sum, category) => sum + category.weight, 0);
  const total =
    rows.reduce((sum, category) => sum + exampleScores[category.code] * category.weight, 0) / weightSum;
  const terms = rows.map((category) => `${exampleScores[category.code]}×${formatPoints(category.weight)}`);

  return (
    <p className="methodology__formula num">
      Без категории «{missing.label}»: ({terms.join(" + ")}) ÷ {formatPoints(weightSum)} = {formatPoints(total)}
    </p>
  );
}

const statusExamples: Array<{ status: Exclude<CategoryStatus, "measured">; text: string }> = [
  {
    status: "unavailable",
    text: "Источник не вернул данных, например скан AppSec не запускался. Оценка становится предварительной.",
  },
  {
    status: "insufficient_sample",
    text: "Данные есть, но их слишком мало для вывода: скажем, репозиторию три недели.",
  },
  {
    status: "error",
    text: "Сбой при сборе. Повторим при следующем анализе, а прошлый результат не пропадёт.",
  },
  {
    status: "not_applicable",
    text: "Категория не относится к репозиторию — например, issues отключены. Оценку предварительной не делает.",
  },
];

const priorityThemes: Record<RecommendationPriority, "danger" | "warning" | "info" | "unknown"> = {
  p0: "danger",
  p1: "warning",
  p2: "info",
  p3: "unknown",
};

const priorityExamples: Array<{ priority: RecommendationPriority; text: string }> = [
  { priority: "p0", text: "подтверждённая критическая проблема безопасности" },
  { priority: "p1", text: "существенный и устойчивый сбой: CI падает, задачи висят месяцами" },
  { priority: "p2", text: "улучшение, которое упростит сопровождение проекта" },
  { priority: "p3", text: "мелочь, которая почти не влияет на оценку" },
];

/** «7 дней», «90 дней» — для порогов активности. */
function daysText(days: number): string {
  return `${days} ${plural(days, "день", "дня", "дней")}`;
}

/** «сутки», «3 суток», «6 часов» — для фразы «раз в …». */
function hoursText(hours: number): string {
  if (hours % 24 === 0) {
    const days = hours / 24;
    return days === 1 ? "сутки" : `${days} суток`;
  }
  return `${hours} ${plural(hours, "час", "часа", "часов")}`;
}
