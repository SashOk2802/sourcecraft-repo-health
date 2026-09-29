import { Text } from "@gravity-ui/uikit";

import type { ReportInsight } from "../../api/report";
import { categoryStatusLabels } from "../../lib/labels";
import { formatInteger, formatShare } from "../../lib/format";
import { EvidenceLinks } from "./EvidenceLinks";
import "./CollaborationInsights.css";

/*
 * Bus factor и качество review — бонус ТЗ со звёздочкой.
 * В Score и список «Что сделать» не входят; expectedScoreDelta здесь нет.
 */

interface CollaborationInsightsProps {
  insights: ReportInsight[];
}

export function CollaborationInsights({ insights }: CollaborationInsightsProps) {
  if (insights.length === 0) {
    return null;
  }

  return (
    <section className="card collaboration" aria-labelledby="collaboration-title">
      <div className="collaboration__head">
        <Text variant="subheader-2" as="h2" id="collaboration-title">
          Сопровождение
        </Text>
        <Text variant="body-1" color="secondary">
          Показатели со звёздочкой: баллы Repo Health Score от них не зависят.
        </Text>
      </div>

      <ul className="collaboration__list">
        {insights.map((insight) => (
          <li className="collaboration__item" key={insight.code}>
            <div className="collaboration__item-head">
              <Text variant="subheader-1" as="h3">
                {insight.label}
              </Text>
              <Text variant="header-1" as="p" className="collaboration__value num">
                {formatInsightValue(insight)}
              </Text>
            </div>
            <Text variant="body-1" color="secondary">
              {categoryStatusLabels[insight.status]}
            </Text>
            <Text variant="body-2" className="collaboration__summary">
              {insight.summary}
            </Text>
            {insight.detail && (
              <Text variant="body-1" color="secondary">
                {insight.detail}
              </Text>
            )}
            {insight.action && (
              <Text variant="body-2" className="collaboration__action">
                Что сделать: {insight.action}
              </Text>
            )}
            <EvidenceLinks items={insight.evidence} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function formatInsightValue(insight: ReportInsight): string {
  if (insight.value === null) {
    return "—";
  }
  if (insight.code === "review_quality") {
    return formatShare(insight.value);
  }
  return formatInteger(insight.value);
}
