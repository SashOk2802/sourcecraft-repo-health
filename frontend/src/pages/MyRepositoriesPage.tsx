import { ArrowUpRightFromSquare } from "@gravity-ui/icons";
import { Button, Icon, Text } from "@gravity-ui/uikit";
import { useEffect, useState } from "react";

import type { AnalysisStatus } from "../api/common";
import {
  fetchAnalysisStatus,
  fetchMyRepositories,
  isAnalysisTerminal,
  startRepositoryAnalysis,
  type AnalysisJob,
  type MyRepository,
} from "../api/myRepositories";
import { ErrorNote, LoadingNote } from "../components/PageNotes";
import { dataOf, useAsync } from "../hooks/useAsync";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { paths } from "../routes";
import "./MyRepositoriesPage.css";

const pollingDelayMs = 1800;

export function MyRepositoriesPage() {
  useDocumentTitle("Мои репозитории");

  const [state, reload] = useAsync(fetchMyRepositories, []);
  const catalog = dataOf(state);
  const [jobs, setJobs] = useState<Record<string, AnalysisJob>>({});
  const [startingIds, setStartingIds] = useState<Set<string>>(() => new Set());
  const [statusErrors, setStatusErrors] = useState<Record<string, Error>>({});

  /*
   * Опрос нужен только для ещё работающих заданий. После terminal-статуса
   * таймер снимается, а результат не перезапрашивается в фоне.
   */
  useEffect(() => {
    const pending = Object.values(jobs).filter((job) => !isAnalysisTerminal(job.status));
    if (pending.length === 0) return;

    let cancelled = false;
    const timer = window.setTimeout(() => {
      void Promise.all(
        pending.map(async (job) => {
          try {
            return { repositoryId: job.repository.id, job: await fetchAnalysisStatus(job.id) };
          } catch (error) {
            return { repositoryId: job.repository.id, error: toError(error) };
          }
        }),
      ).then((updates) => {
        if (cancelled) return;

        setJobs((current) => {
          const next = { ...current };
          for (const update of updates) {
            if ("job" in update) next[update.repositoryId] = update.job;
          }
          return next;
        });
        setStatusErrors((current) => {
          const next = { ...current };
          for (const update of updates) {
            if ("job" in update) delete next[update.repositoryId];
            else next[update.repositoryId] = update.error;
          }
          return next;
        });
      });
    }, pollingDelayMs);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [jobs]);

  async function start(repositoryId: string): Promise<void> {
    setStartingIds((current) => new Set(current).add(repositoryId));
    setStatusErrors((current) => {
      const next = { ...current };
      delete next[repositoryId];
      return next;
    });

    try {
      const job = await startRepositoryAnalysis(repositoryId);
      setJobs((current) => ({ ...current, [repositoryId]: job }));
    } catch (error) {
      setStatusErrors((current) => ({ ...current, [repositoryId]: toError(error) }));
    } finally {
      setStartingIds((current) => {
        const next = new Set(current);
        next.delete(repositoryId);
        return next;
      });
    }
  }

  async function refresh(repositoryId: string, analysisId: string): Promise<void> {
    try {
      const job = await fetchAnalysisStatus(analysisId);
      setJobs((current) => ({ ...current, [repositoryId]: job }));
      setStatusErrors((current) => {
        const next = { ...current };
        delete next[repositoryId];
        return next;
      });
    } catch (error) {
      setStatusErrors((current) => ({ ...current, [repositoryId]: toError(error) }));
    }
  }

  return (
    <div className="page__inner">
      <div className="page__header">
        <div>
          <Text variant="header-2" as="h1" className="page__title">
            Мои репозитории
          </Text>
          <Text variant="body-2" color="secondary" className="my-repositories__about">
            Здесь показан публичный каталог настроенных организаций SourceCraft. В первой версии доступ к
            private и internal репозиториям не выдаётся через Яндекс ID.
          </Text>
        </div>
      </div>

      {state.status === "error" && (
        <ErrorNote title="Не удалось загрузить репозитории" error={state.error} onRetry={reload} />
      )}
      {!catalog && state.status === "loading" && <LoadingNote>Загружаем репозитории</LoadingNote>}

      {catalog && (
        <>
          {catalog.repositories.length === 0 ? (
            <section className="card my-repositories__empty">
              <Text variant="subheader-2" as="h2">
                Репозиториев пока нет
              </Text>
              <Text variant="body-2" color="secondary">
                Добавьте публичную организацию в SOURCECRAFT_PUBLIC_ORGANIZATIONS и обновите страницу.
              </Text>
            </section>
          ) : (
            <div className="my-repositories__table-wrap">
              <table className="my-repositories__table">
                <thead>
                  <tr>
                    <th scope="col">Репозиторий</th>
                    <th scope="col">Ветка</th>
                    <th scope="col">Язык</th>
                    <th scope="col">Анализ</th>
                  </tr>
                </thead>
                <tbody>
                  {catalog.repositories.map((repository) => (
                    <RepositoryRow
                      key={repository.id}
                      repository={repository}
                      job={jobs[repository.id]}
                      starting={startingIds.has(repository.id)}
                      statusError={statusErrors[repository.id]}
                      onStart={start}
                      onRefresh={refresh}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <Text variant="body-1" color="secondary" className="my-repositories__notice">
            После запуска анализ продолжится на backend, даже если закрыть страницу. Готовый отчёт появится в
            этой строке.
          </Text>
        </>
      )}
    </div>
  );
}

interface RepositoryRowProps {
  repository: MyRepository;
  job: AnalysisJob | undefined;
  starting: boolean;
  statusError: Error | undefined;
  onStart: (repositoryId: string) => Promise<void>;
  onRefresh: (repositoryId: string, analysisId: string) => Promise<void>;
}

function RepositoryRow({ repository, job, starting, statusError, onStart, onRefresh }: RepositoryRowProps) {
  return (
    <tr>
      <td>
        <span className="my-repositories__name">
          {repository.name}
          {repository.url && (
            <a
              className="my-repositories__external"
              href={repository.url}
              target="_blank"
              rel="noreferrer"
              title="Открыть репозиторий в SourceCraft"
              aria-label={`Открыть ${repository.name} в SourceCraft`}
            >
              <Icon data={ArrowUpRightFromSquare} size={14} />
            </a>
          )}
        </span>
        {repository.isEmpty && (
          <Text variant="body-1" color="secondary" className="my-repositories__meta">
            В репозитории пока нет файлов
          </Text>
        )}
      </td>
      <td className="num">{repository.defaultBranch ?? "—"}</td>
      <td>{repository.language ?? "—"}</td>
      <td>
        <AnalysisAction
          repository={repository}
          job={job}
          starting={starting}
          statusError={statusError}
          onStart={onStart}
          onRefresh={onRefresh}
        />
      </td>
    </tr>
  );
}

interface AnalysisActionProps {
  repository: MyRepository;
  job: AnalysisJob | undefined;
  starting: boolean;
  statusError: Error | undefined;
  onStart: (repositoryId: string) => Promise<void>;
  onRefresh: (repositoryId: string, analysisId: string) => Promise<void>;
}

function AnalysisAction({ repository, job, starting, statusError, onStart, onRefresh }: AnalysisActionProps) {
  const actionLabel = job && isAnalysisTerminal(job.status) ? "Проверить снова" : "Проверить";

  if (starting) {
    return (
      <div className="my-repositories__action" aria-live="polite">
        <Button view="outlined" disabled>
          Создаём задачу…
        </Button>
      </div>
    );
  }

  if (job && !isAnalysisTerminal(job.status)) {
    return (
      <div className="my-repositories__action" aria-live="polite">
        <Button view="outlined" disabled>
          Анализ выполняется
        </Button>
        <Text variant="body-1" color="secondary" className="my-repositories__status">
          {statusLabel(job.status)}
        </Text>
        {statusError && (
          <>
            <Text variant="body-1" className="my-repositories__status my-repositories__status_error">
              {statusError.message}
            </Text>
            <Button view="outlined" size="s" onClick={() => void onRefresh(repository.id, job.id)}>
              Обновить статус
            </Button>
          </>
        )}
      </div>
    );
  }

  if (job && (job.status === "completed" || job.status === "partial")) {
    return (
      <div className="my-repositories__action">
        <Button view="action" href={paths.analysis(job.id)}>
          Открыть отчёт
        </Button>
        <Text variant="body-1" className="my-repositories__status my-repositories__status_done">
          {job.status === "partial" ? "Предварительная оценка" : "Анализ завершён"}
        </Text>
        <Button view="outlined" size="s" onClick={() => void onStart(repository.id)}>
          {actionLabel}
        </Button>
      </div>
    );
  }

  return (
    <div className="my-repositories__action">
      <Button view={job?.status === "failed" ? "outlined" : "action"} onClick={() => void onStart(repository.id)}>
        {actionLabel}
      </Button>
      {job?.status === "failed" && job.error && (
        <Text variant="body-1" className="my-repositories__status my-repositories__status_error">
          {job.error.summary}
        </Text>
      )}
      {statusError && (
        <Text variant="body-1" className="my-repositories__status my-repositories__status_error">
          {statusError.message}
        </Text>
      )}
    </div>
  );
}

function statusLabel(status: AnalysisStatus): string {
  switch (status) {
    case "queued":
      return "Задание ждёт свободного обработчика";
    case "running":
    case "collecting":
      return "Собираем данные из SourceCraft";
    case "calculating":
      return "Считаем Score и рекомендации";
    default:
      return "Обновляем состояние анализа";
  }
}

function toError(error: unknown): Error {
  return error instanceof Error ? error : new Error("Не удалось обновить состояние анализа.");
}
