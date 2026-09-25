import { ArrowRotateLeft, ArrowUpRightFromSquare, Printer } from "@gravity-ui/icons";
import { Button, Icon, Link as GravityLink, Text } from "@gravity-ui/uikit";
import { useEffect, useState } from "react";

import { fetchAnalysisStatus, isAnalysisFinished, type AnalysisStatusResponse } from "../api/analyses";
import { ApiError, describeError } from "../api/http";
import { mocksEnabled } from "../api/mockMode";
import { fetchReport, type RepositoryReport } from "../api/report";
import { useAuth } from "../auth/AuthContext";
import { ErrorNote, LoadingNote } from "../components/PageNotes";
import { AnalysisFacts } from "../components/report/AnalysisFacts";
import { AnalysisFailed, AnalysisProgress } from "../components/report/AnalysisProgress";
import { CategoryMarks } from "../components/report/CategoryMarks";
import { MarkdownExport } from "../components/report/MarkdownExport";
import { ProjectHighlights } from "../components/report/ProjectHighlights";
import { RecommendationList } from "../components/report/RecommendationList";
import { ScoreCard } from "../components/report/ScoreCard";
import { dataOf, useAsync } from "../hooks/useAsync";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useStartAnalysis } from "../hooks/useStartAnalysis";
import { formatDateTime } from "../lib/format";
import { Link, navigate } from "../router";
import { paths } from "../routes";
import "./AnalysisPage.css";

// Архитектура предлагает опрос раз в несколько секунд; mock-анализ короткий, поэтому чаще.
const POLL_INTERVAL_MS = mocksEnabled ? 1_000 : 3_000;

export function AnalysisPage({ analysisId }: { analysisId: string }) {
  const { analysis, error: statusError, retry } = useAnalysisStatus(analysisId);
  const finished = analysis !== null && isAnalysisFinished(analysis.status);
  const failed = analysis !== null && (analysis.status === "failed" || analysis.status === "cancelled");
  const now = useNow(analysis !== null && !finished);
  const restart = useStartAnalysis();

  const [reportState, reloadReport] = useAsync(
    () => (finished && !failed ? fetchReport(analysisId) : Promise.resolve(null)),
    [analysisId, finished, failed],
  );
  const report = dataOf(reportState) ?? null;

  // Пока анализ в очереди, backend знает только id репозитория, без имени.
  const title = analysis?.repository.name ?? report?.repository.name ?? "Анализ репозитория";
  useDocumentTitle(title);

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
          {title}
        </Text>
      </nav>

      {!analysis && (
        <section className="card">
          {statusError === null && <LoadingNote>Загружаем анализ</LoadingNote>}
          {statusError !== null && isAccessProblem(statusError) && <NoReportNote signInRequired={isSignInRequired(statusError)} />}
          {statusError !== null && !isAccessProblem(statusError) && (
            <ErrorNote title="Не удалось получить статус анализа" error={statusError} onRetry={retry} />
          )}
        </section>
      )}

      {analysis && !finished && (
        <AnalysisProgress
          analysis={analysis}
          elapsedSeconds={(now - Date.parse(analysis.createdAt ?? new Date().toISOString())) / 1000}
        />
      )}

      {analysis && failed && (
        <AnalysisFailed
          analysis={analysis}
          restarting={restart.startingId !== null}
          onRestart={() => void restart.start(analysis.repository.id)}
        />
      )}

      {analysis && finished && !failed && !report && (
        <section className="card">
          {reportState.status === "error" && isAccessProblem(reportState.error) ? (
            <NoReportNote signInRequired={isSignInRequired(reportState.error)} />
          ) : reportState.status === "error" ? (
            <ErrorNote title="Не удалось загрузить отчёт" error={reportState.error} onRetry={reloadReport} />
          ) : (
            <LoadingNote>Загружаем отчёт</LoadingNote>
          )}
        </section>
      )}

      {report && <ReportView report={report} />}
    </div>
  );
}

function ReportView({ report }: { report: RepositoryReport }) {
  const { repository, analysis } = report;
  const auth = useAuth();
  const rerun = useStartAnalysis();

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
          {auth.status === "signedIn" && (
            <Button
              view="action"
              loading={rerun.startingId !== null}
              onClick={() => void rerun.start(repository.id)}
            >
              <Icon data={ArrowRotateLeft} size={16} />
              Проверить снова
            </Button>
          )}
          <Button view="outlined" onClick={() => window.print()}>
            <Icon data={Printer} size={16} />
            Печать / сохранить PDF
          </Button>
          <MarkdownExport report={report} />
        </div>
      </header>

      {rerun.error && (
        <Text variant="body-2" color="danger" className="report__rerun-error">
          Не удалось запустить анализ. {describeError(rerun.error)}
        </Text>
      )}

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

/*
 * 401 и 404 — не сбой. Статус и отчёт backend отдаёт только тому, кто запускал анализ
 * (docs/api-contract.md): без входа — 401, чужой или несуществующий анализ — одинаковый 404.
 */
function NoReportNote({ signInRequired }: { signInRequired: boolean }) {
  const auth = useAuth();
  const offerSignIn = signInRequired || auth.status === "guest";

  return (
    <div className="no-report">
      <Text variant="header-2" as="h1">
        {signInRequired ? "Отчёт виден после входа" : "Отчёта нет"}
      </Text>
      <Text variant="body-2" color="secondary">
        {signInRequired
          ? "Ход и результат анализа видит тот, кто его запускал. Войдите через Яндекс ID — если анализ ваш, отчёт откроется."
          : "Такого анализа нет, ссылка устарела или анализ запускал другой пользователь — его отчёт видит только он."}{" "}
        Открытые проекты других команд — в рейтинге.
      </Text>
      <div className="no-report__actions">
        {offerSignIn && (
          <Button view="action" size="l" onClick={() => auth.signIn(window.location.pathname)}>
            Войти через Яндекс ID
          </Button>
        )}
        <Button view="outlined" size="l" onClick={() => navigate(paths.leaderboard())}>
          К рейтингу
        </Button>
      </div>
    </div>
  );
}

/** Опрашивает статус, пока анализ не завершится. Временные сбои сети не останавливают опрос. */
function useAnalysisStatus(analysisId: string) {
  const [analysis, setAnalysis] = useState<AnalysisStatusResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    const poll = (): void => {
      fetchAnalysisStatus(analysisId).then(
        (next) => {
          if (cancelled) return;
          setAnalysis(next);
          setError(null);
          if (!isAnalysisFinished(next.status)) {
            timer = window.setTimeout(poll, POLL_INTERVAL_MS);
          }
        },
        (reason: unknown) => {
          if (cancelled) return;
          const failure = reason instanceof Error ? reason : new Error(String(reason));
          setError(failure);
          const permanent = failure instanceof ApiError && [401, 403, 404].includes(failure.status);
          if (!permanent) {
            timer = window.setTimeout(poll, POLL_INTERVAL_MS * 2);
          }
        },
      );
    };

    poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [analysisId, attempt]);

  return { analysis, error, retry: () => setAttempt((value) => value + 1) };
}

/** Текущее время с обновлением раз в секунду — для счётчика «идёт 0:07». */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [active]);

  return now;
}

function isSignInRequired(error: Error): boolean {
  return error instanceof ApiError && error.status === 401;
}

function isAccessProblem(error: Error): boolean {
  return error instanceof ApiError && (error.status === 401 || error.status === 404);
}
