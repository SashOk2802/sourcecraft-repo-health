import { ArrowUpRightFromSquare, Printer } from "@gravity-ui/icons";
import { Button, Icon, Link as GravityLink, Text } from "@gravity-ui/uikit";

import { ApiError } from "../api/http";
import { fetchReport, type RepositoryReport } from "../api/report";
import { ErrorNote, LoadingNote } from "../components/PageNotes";
import { AnalysisFacts } from "../components/report/AnalysisFacts";
import { CategoryMarks } from "../components/report/CategoryMarks";
import { MarkdownExport } from "../components/report/MarkdownExport";
import { ProjectHighlights } from "../components/report/ProjectHighlights";
import { RecommendationList } from "../components/report/RecommendationList";
import { ScoreCard } from "../components/report/ScoreCard";
import { dataOf, useAsync } from "../hooks/useAsync";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { formatDateTime } from "../lib/format";
import { Link, navigate } from "../router";
import { paths } from "../routes";
import "./AnalysisPage.css";

export function AnalysisPage({ analysisId }: { analysisId: string }) {
  const [state, reload] = useAsync(() => fetchReport(analysisId), [analysisId]);
  const report = dataOf(state);
  useDocumentTitle(report ? report.repository.name : `Анализ ${analysisId}`);

  return (
    <div className="page__inner">
      <nav className="page__breadcrumbs" aria-label="Навигация">
        <Text variant="body-1" color="secondary">
          <Link className="breadcrumb" to={paths.leaderboard()}>
            Рейтинг
          </Link>
          <span className="breadcrumb__separator" aria-hidden="true">
            /
          </span>
          {report ? report.repository.name : analysisId}
        </Text>
      </nav>

      {report ? (
        <ReportView report={report} />
      ) : (
        <section className="card">
          {state.status === "loading" && <LoadingNote>Загружаем отчёт</LoadingNote>}
          {state.status === "error" && isNotFound(state.error) && <NoReportNote />}
          {state.status === "error" && !isNotFound(state.error) && (
            <ErrorNote title="Не удалось загрузить отчёт" error={state.error} onRetry={reload} />
          )}
        </section>
      )}
    </div>
  );
}

function ReportView({ report }: { report: RepositoryReport }) {
  const { repository, analysis } = report;

  return (
    <article className="report">
      <header className="report__head">
        <div className="report__title">
          <Text variant="header-2" as="h1">
            <span className="report__org">{repository.organizationSlug} / </span>
            {repository.repositorySlug}
          </Text>
          <div className="report__meta">
            {repository.url && (
              <GravityLink href={repository.url} target="_blank" rel="noreferrer">
                Открыть в SourceCraft <Icon data={ArrowUpRightFromSquare} size={12} />
              </GravityLink>
            )}
            <Text variant="body-1" color="secondary">
              Анализ от {formatDateTime(analysis.analyzedAt)}
            </Text>
            {analysis.commitSha && (
              <Text variant="body-1" color="secondary" className="num">
                коммит {analysis.commitSha.slice(0, 7)}
              </Text>
            )}
          </div>
        </div>

        <div className="report__actions">
          <Button view="outlined" onClick={() => window.print()}>
            <Icon data={Printer} size={16} />
            Печать / сохранить PDF
          </Button>
          <MarkdownExport report={report} />
        </div>
      </header>

      <div className="report__grid">
        <div className="report__main">
          <ScoreCard report={report} />
          <ProjectHighlights categories={report.categories} />
          <CategoryMarks categories={report.categories} />
          <RecommendationList recommendations={report.recommendations} hasScore={report.score !== null} />
        </div>
        <aside className="report__aside">
          <AnalysisFacts analysis={analysis} scoreDetails={report.scoreDetails} />
        </aside>
      </div>
    </article>
  );
}

/** 404 — не сбой: такого снимка анализа нет. */
function NoReportNote() {
  return (
    <div className="no-report">
      <Text variant="header-2" as="h1">
        Отчёта нет
      </Text>
      <Text variant="body-2" color="secondary">
        Такого анализа не существует или ссылка устарела. Откройте репозиторий из рейтинга — там всегда ссылка на
        последний отчёт.
      </Text>
      <div>
        <Button view="action" size="l" onClick={() => navigate(paths.leaderboard())}>
          К рейтингу
        </Button>
      </div>
    </div>
  );
}

function isNotFound(error: Error): boolean {
  return error instanceof ApiError && error.status === 404;
}
