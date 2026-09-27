import { Circle, CircleCheckFill, CircleMinus, CircleXmarkFill } from "@gravity-ui/icons";
import { Button, Icon, Spin, Text } from "@gravity-ui/uikit";

import type { AnalysisStage, AnalysisStatusResponse } from "../../api/analyses";
import { cn } from "../../lib/classNames";
import { formatDuration } from "../../lib/format";
import { analysisStatusLabels } from "../../lib/labels";
import "./AnalysisProgress.css";

const stageStatusLabels: Record<AnalysisStage["status"], string> = {
  pending: "ждёт",
  running: "идёт",
  done: "готово",
  unavailable: "нет данных",
  error: "ошибка",
};

interface AnalysisProgressProps {
  analysis: AnalysisStatusResponse;
  /** Сколько секунд идёт анализ. */
  elapsedSeconds: number;
}

/** Ход анализа: точного процента нет — объём работы заранее неизвестен. */
export function AnalysisProgress({ analysis, elapsedSeconds }: AnalysisProgressProps) {
  return (
    <section className="card analysis-progress">
      <div className="analysis-progress__head" role="status" aria-live="polite">
        <div className="analysis-progress__state">
          <Spin size="s" />
          <Text variant="subheader-2">{capitalize(analysisStatusLabels[analysis.status] ?? analysis.status)}</Text>
        </div>
        <Text variant="body-1" color="secondary" className="num">
          идёт {formatDuration(elapsedSeconds)}
        </Text>
      </div>

      {analysis.stages && analysis.stages.length > 0 && (
        <ol className="stages">
          {analysis.stages.map((stage) => (
            <li key={stage.code} className={cn("stage", `stage_status_${stage.status}`)}>
              <StageIcon status={stage.status} />
              <Text variant="body-2">{stage.label}</Text>
              <Text variant="body-1" color="secondary">
                {stage.summary ?? stageStatusLabels[stage.status]}
              </Text>
            </li>
          ))}
        </ol>
      )}

      {/* Кабинет находит этот анализ по памяти браузера (lib/recentAnalyses.ts), даже если backend его не отдаёт. */}
      <Text variant="body-1" color="secondary">
        Страницу можно закрыть: анализ продолжится на сервере. Отчёт откроется по этой ссылке и в «Моих репозиториях».
      </Text>
    </section>
  );
}

function StageIcon({ status }: { status: AnalysisStage["status"] }) {
  if (status === "running") {
    return <Spin size="xs" />;
  }
  const icons = {
    done: CircleCheckFill,
    error: CircleXmarkFill,
    unavailable: CircleMinus,
    pending: Circle,
  } as const;
  return <Icon data={icons[status]} size={16} className="stage__icon" />;
}

interface AnalysisFailedProps {
  analysis: AnalysisStatusResponse;
  restarting: boolean;
  onRestart: () => void;
}

export function AnalysisFailed({ analysis, restarting, onRestart }: AnalysisFailedProps) {
  return (
    <section className="card analysis-progress">
      <Text variant="subheader-2" as="h2">
        {analysis.status === "failed" ? "Анализ не удался" : "Анализ отменён"}
      </Text>
      <Text variant="body-2" color="secondary">
        {analysis.error?.summary ?? "Проверка не дошла до конца."} Прошлый отчёт, если он был, не пропал — обычно
        помогает запустить анализ ещё раз.
      </Text>
      <div>
        <Button view="action" size="l" loading={restarting} onClick={onRestart}>
          Запустить снова
        </Button>
      </div>
    </section>
  );
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
