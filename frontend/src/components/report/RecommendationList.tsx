import { Label, Text } from "@gravity-ui/uikit";

import type { Recommendation } from "../../api/report";
import type { RecommendationPriority } from "../../api/common";
import { formatPoints } from "../../lib/format";
import { priorityLabels } from "../../lib/labels";
import { EvidenceLinks } from "./EvidenceLinks";
import "./RecommendationList.css";

const priorityThemes: Record<RecommendationPriority, "danger" | "warning" | "info" | "unknown"> = {
  p0: "danger",
  p1: "warning",
  p2: "info",
  p3: "unknown",
};

interface RecommendationListProps {
  recommendations: Recommendation[];
  /** Есть ли у отчёта оценка: от этого зависит текст пустого состояния. */
  hasScore: boolean;
}

export function RecommendationList({ recommendations, hasScore }: RecommendationListProps) {
  return (
    <section className="section">
      <div className="section__head">
        <Text variant="subheader-2" as="h2">
          Что сделать
        </Text>
        {recommendations.length > 0 && (
          <Text variant="body-1" color="secondary">
            сверху — самое срочное
          </Text>
        )}
      </div>

      {recommendations.length === 0 ? (
        <Text variant="body-2" color="secondary">
          {hasScore
            ? "Рекомендаций нет: по собранным данным срочно исправлять нечего."
            : "Рекомендаций пока нет: без данных не на что опереться. Они появятся после анализа с доступом к источникам."}
        </Text>
      ) : (
        <>
          <ol className="recommendations">
            {recommendations.map((recommendation, index) => (
              <li className="recommendation" key={recommendation.code}>
                <span className="recommendation__index num">#{index + 1}</span>
                <span className="recommendation__priority">
                  <Label theme={priorityThemes[recommendation.priority]} size="s">
                    {priorityLabels[recommendation.priority]}
                  </Label>
                </span>
                <div className="recommendation__body">
                  <Text variant="subheader-1" as="h3">
                    {recommendation.action}
                  </Text>
                  <Text variant="body-2" color="secondary" className="recommendation__text">
                    {recommendation.problem} {recommendation.rationale}
                  </Text>
                  <EvidenceLinks items={recommendation.evidence} />
                  {recommendation.expectedEffect && (
                    <Text variant="body-1" color="secondary" className="recommendation__effect">
                      Что изменится: {recommendation.expectedEffect}
                    </Text>
                  )}
                </div>
                <div className="recommendation__gain">
                  {recommendation.expectedScoreDelta !== null && recommendation.expectedScoreDelta > 0 && (
                    <>
                      <span className="recommendation__delta num">
                        +{formatPoints(recommendation.expectedScoreDelta)}
                      </span>
                      <Text variant="body-1" color="secondary">
                        к Score
                      </Text>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ol>
          <Text variant="body-1" color="secondary" className="recommendations__note">
            Возможные приросты не суммируются: рекомендации могут влиять на одни и те же метрики или снять общее
            ограничение Score.
          </Text>
        </>
      )}
    </section>
  );
}
