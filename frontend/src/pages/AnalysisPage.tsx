import { WorkInProgress } from "./WorkInProgress";

export function AnalysisPage({ analysisId }: { analysisId: string }) {
  return <WorkInProgress title={`Анализ ${analysisId}`} />;
}
