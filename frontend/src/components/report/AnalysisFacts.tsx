import { Text } from "@gravity-ui/uikit";

import type { ReportAnalysis, ScoreDetails } from "../../api/report";
import { formatDateTime, formatPoints, formatShare } from "../../lib/format";
import { analysisStatusLabels } from "../../lib/labels";
import "./AnalysisFacts.css";

interface AnalysisFactsProps {
  analysis: ReportAnalysis;
  scoreDetails: ScoreDetails;
}

/** Правая колонка отчёта: на каком снимке он построен. */
export function AnalysisFacts({ analysis, scoreDetails }: AnalysisFactsProps) {
  const facts: Array<{ term: string; value: string }> = [
    { term: "Статус", value: analysisStatusLabels[analysis.status] ?? analysis.status },
    { term: "Дата анализа", value: formatDateTime(analysis.analyzedAt) },
    { term: "Методика", value: analysis.methodologyVersion },
    {
      term: "Измерено",
      value: `${formatPoints(scoreDetails.measuredWeight)}% из ${formatPoints(scoreDetails.applicableWeight)}% веса`,
    },
  ];

  if (analysis.coverage !== null) {
    facts.push({ term: "Полнота", value: formatShare(analysis.coverage) });
  }
  if (analysis.commitSha) {
    facts.push({ term: "Коммит", value: analysis.commitSha.slice(0, 7) });
  }
  facts.push({ term: "Идентификатор", value: analysis.id });

  return (
    <section className="card analysis-facts">
      <Text variant="subheader-2" as="h2">
        Об анализе
      </Text>
      <dl className="analysis-facts__list">
        {facts.map((fact) => (
          <div className="analysis-facts__row" key={fact.term}>
            <dt>
              <Text variant="body-1" color="secondary">
                {fact.term}
              </Text>
            </dt>
            <dd>
              <Text variant="body-2" className="num">
                {fact.value}
              </Text>
            </dd>
          </div>
        ))}
      </dl>
      <Text variant="body-1" color="secondary">
        Отчёт построен на неизменяемом снимке: по этой ссылке он всегда будет таким же.
      </Text>
    </section>
  );
}
