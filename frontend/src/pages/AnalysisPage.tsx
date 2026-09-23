import { ArrowRotateLeft, ArrowUpRightFromSquare, Printer } from "@gravity-ui/icons";
import { Button, Icon, Link as GravityLink, Text } from "@gravity-ui/uikit";
import { useEffect, useState } from "react";

import { fetchAnalysisStatus, isAnalysisFinished, type AnalysisStatusResponse } from "../api/analyses";
import { usesDemo } from "../api/dataSource";
import { ApiError, describeError } from "../api/http";
import { fetchReport, type RepositoryReport } from "../api/report";
import { signInUnavailableHint, useAuth } from "../auth/AuthContext";
import { DemoNote } from "../components/DemoNote";
import { ErrorNote, LoadingNote } from "../components/PageNotes";
import { AnalysisFacts } from "../components/report/AnalysisFacts";
import { AnalysisFailed, AnalysisProgress } from "../components/report/AnalysisProgress";
import { BadgeSnippet } from "../components/report/BadgeSnippet";
import { CategoryMarks } from "../components/report/CategoryMarks";
import { MarkdownExport } from "../components/report/MarkdownExport";
import { ProjectHighlights } from "../components/report/ProjectHighlights";
import { RecommendationList } from "../components/report/RecommendationList";
import { ScoreCard } from "../components/report/ScoreCard";
import { dataOf, useAsync } from "../hooks/useAsync";
import { usePageMeta } from "../hooks/usePageMeta";
import { useStartAnalysis } from "../hooks/useStartAnalysis";
import { formatDateTime, formatScore } from "../lib/format";
import { Link, navigate } from "../router";
import { paths } from "../routes";
import "./AnalysisPage.css";

// Архитектура предлагает опрос раз в несколько секунд; демо-анализ короткий, поэтому чаще.
function pollInterval(analysisId: string): number {
  return usesDemo(analysisId) ? 1_000 : 3_000;
}

export function AnalysisPage({ analysisId }: { analysisId: string }) {
  const { analysis, error: statusError, retry } = useAnalysisStatus(analysisId);
  const finished = analysis !== null && isAnalysisFinished(analysis.status);
  const failed = analysis !== null && (analysis.status === "failed" || analysis.status === "cancelled");
  const now = useNow(analysis !== null && !finished);
  const restart = useStartAnalysis();
  const auth = useAuth();

  const [reportState, reloadReport] = useAsync(
    () => (finished && !failed ? fetchReport(analysisId) : Promise.resolve(null)),
    [analysisId, finished, failed],
  );
  const report = dataOf(reportState) ?? null;

  // Пока анализ в очереди, backend знает только id репозитория, без имени.
  const title = analysis?.repository.name ?? report?.repository.name ?? "Анализ репозитория";
  usePageMeta({
    title,
    description: report ? describeReport(report) : undefined,
    // В поиск — только готовые отчёты по настоящим репозиториям.
    noindex: !report || usesDemo(analysisId),
  });

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
          {statusError !== null && isNotFound(statusError) && <NoReportNote />}
          {statusError !== null && !isNotFound(statusError) && (
            <ErrorNote title="Не удалось получить статус анализа" error={statusError} onRetry={retry} />
          )}
        </section>
      )}

      {/* Пока отчёта нет, заголовок страницы нужен хотя бы скринридеру. */}
      {analysis && !report && <h1 className="visually-hidden">{title}</h1>}

      {analysis && !finished && usesDemo(analysisId) && (
        <DemoNote>Демо-анализ вымышленного репозитория: этапы и результат показаны на примере.</DemoNote>
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
          canRestart={auth.status === "signedIn"}
          restarting={restart.startingId !== null}
          restartError={restart.error}
          onRestart={() => void restart.start(analysis.repository.id)}
        />
      )}

      {analysis && finished && !failed && !report && (
        <section className="card">
          {reportState.status === "error" ? (
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
          {usesDemo(analysis.id) && (
            <DemoNote className="report__demo">
              Отчёт по вымышленному репозиторию — так выглядит результат анализа.
            </DemoNote>
          )}
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
          <BadgeSnippet report={report} />
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

/** 404 — не сбой: такого снимка анализа нет. */
function NoReportNote() {
  const auth = useAuth();
  // Закрытый отчёт откроет только настоящий вход: демо-кабинет чужих репозиториев не видит.
  const suggestSignIn = auth.status === "guest" && auth.mode !== "demo";

  return (
    <div className="no-report">
      <Text variant="header-2" as="h1">
        Отчёта нет
      </Text>
      <Text variant="body-2" color="secondary">
        Такого анализа не существует или ссылка устарела. Откройте репозиторий из рейтинга — там всегда ссылка на
        последний отчёт.
        {suggestSignIn && " Если это ваш закрытый репозиторий, войдите через Яндекс ID."}
      </Text>
      <div className="no-report__actions">
        {suggestSignIn && (
          <Button
            view="action"
            size="l"
            disabled={auth.mode === "offline"}
            title={auth.mode === "offline" ? signInUnavailableHint : undefined}
            onClick={() => auth.signIn(window.location.pathname)}
          >
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
            timer = window.setTimeout(poll, pollInterval(analysisId));
          }
        },
        (reason: unknown) => {
          if (cancelled) return;
          const failure = reason instanceof Error ? reason : new Error(String(reason));
          setError(failure);
          const permanent = failure instanceof ApiError && [401, 403, 404].includes(failure.status);
          if (!permanent) {
            timer = window.setTimeout(poll, pollInterval(analysisId) * 2);
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

/** Описание для поиска и превью ссылки на отчёт. */
function describeReport(report: RepositoryReport): string {
  const score =
    report.score === null ? "Оценку пока не из чего посчитать" : `Repo Health Score ${formatScore(report.score)} из 100`;
  return `${score} для ${report.repository.name}: оценка по шести частям проекта, сильные и слабые стороны, рекомендации.`;
}

function isNotFound(error: Error): boolean {
  return error instanceof ApiError && error.status === 404;
}
