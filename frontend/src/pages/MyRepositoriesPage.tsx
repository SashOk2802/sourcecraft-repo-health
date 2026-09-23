import { Alert, Button, Label, Link as GravityLink, Text, TextInput } from "@gravity-ui/uikit";
import { useState } from "react";

import {
  connectDemoSourceCraft,
  connectSourceCraft,
  disconnectSourceCraft,
  fetchSourceCraftConnection,
  type SourceCraftConnection,
} from "../api/connections";
import { describeError } from "../api/http";
import { fetchMyRepositories, type MyRepository } from "../api/me";
import { signInUnavailableHint, useAuth } from "../auth/AuthContext";
import { DemoNote } from "../components/DemoNote";
import { ErrorNote, LoadingNote } from "../components/PageNotes";
import { dataOf, useAsync } from "../hooks/useAsync";
import { usePageMeta } from "../hooks/usePageMeta";
import { useRecentAnalyses } from "../hooks/useRecentAnalyses";
import { useStartAnalysis } from "../hooks/useStartAnalysis";
import { cn } from "../lib/classNames";
import { formatDateTimeCompact, formatScore } from "../lib/format";
import { getScoreBand } from "../lib/scoreBands";
import { Link, spaLinkProps } from "../router";
import { paths } from "../routes";
import "./MyRepositoriesPage.css";

export function MyRepositoriesPage() {
  const auth = useAuth();
  // Личный кабинет поисковику не нужен.
  usePageMeta({ title: "Мои репозитории", noindex: true });
  const demo = auth.mode === "demo";

  return (
    <div className="page__inner">
      <div className="page__header">
        <div>
          <Text variant="header-2" as="h1" className="page__title">
            Мои репозитории
          </Text>
          <Text variant="body-2" color="secondary" className="page__lead">
            Репозитории SourceCraft, к которым у вас есть доступ. Отчёты по закрытым видите только вы.
          </Text>
          {demo && (
            <DemoNote className="my-repos__demo">
              Демо-кабинет: вход и репозитории показаны на примере, настоящий токен SourceCraft не нужен.
            </DemoNote>
          )}
        </div>
      </div>

      {auth.status === "unknown" && <LoadingNote>Проверяем вход</LoadingNote>}
      {auth.status === "guest" && (
        <SignInInvite unavailable={auth.mode === "offline"} onSignIn={() => auth.signIn(paths.myRepositories())} />
      )}
      {auth.status === "signedIn" && <ConnectedArea demo={demo} />}
    </div>
  );
}

function SignInInvite({ unavailable, onSignIn }: { unavailable: boolean; onSignIn: () => void }) {
  return (
    <section className="card my-repos__invite">
      <Text variant="subheader-2" as="h2">
        Войдите через Яндекс ID
      </Text>
      <Text variant="body-2" color="secondary">
        Яндекс ID подтверждает, кто вы. Чтобы мы увидели ваши репозитории, после входа нужно будет подключить
        SourceCraft личным токеном.
      </Text>
      <ol className="my-repos__steps">
        <li>Войдите через Яндекс ID.</li>
        <li>Подключите SourceCraft: токен уйдёт на сервер один раз и в браузер не вернётся.</li>
        <li>Выберите репозиторий — открытый или закрытый — и запустите проверку.</li>
        <li>Через пару минут получите оценку, объяснение и список действий.</li>
      </ol>
      <div className="my-repos__invite-actions">
        <Button view="action" size="l" disabled={unavailable} onClick={onSignIn}>
          Войти через Яндекс ID
        </Button>
        {unavailable && (
          <Text variant="body-1" color="secondary">
            {signInUnavailableHint}.
          </Text>
        )}
      </div>
    </section>
  );
}

function ConnectedArea({ demo }: { demo: boolean }) {
  const [state, reload] = useAsync(fetchSourceCraftConnection, []);
  const connection = dataOf(state);

  if (!connection) {
    return state.status === "error" ? (
      <ErrorNote title="Не удалось проверить подключение к SourceCraft" error={state.error} onRetry={reload} />
    ) : (
      <LoadingNote>Проверяем подключение к SourceCraft</LoadingNote>
    );
  }

  if (!connection.connected) {
    return <ConnectForm demo={demo} onConnected={reload} />;
  }

  return (
    <>
      <ConnectionBar connection={connection} onDisconnected={reload} />
      <RepositoryList />
    </>
  );
}

function ConnectForm({ demo, onConnected }: { demo: boolean; onConnected: () => void }) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  async function submit(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      // В демо токен не спрашиваем: на стенде никто не должен вводить настоящий.
      await (demo ? connectDemoSourceCraft() : connectSourceCraft(token));
      setToken("");
      onConnected();
    } catch (reason) {
      setError(reason instanceof Error ? reason : new Error(String(reason)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card my-repos__connect">
      <Text variant="subheader-2" as="h2">
        Подключите SourceCraft
      </Text>
      <Text variant="body-2" color="secondary">
        Вход через Яндекс ID не даёт нам доступ к вашим репозиториям. Нужен личный токен SourceCraft: создайте его в
        настройках профиля SourceCraft и вставьте сюда. Токен уходит на наш сервер один раз, хранится зашифрованно
        и обратно в браузер не возвращается.
      </Text>

      <form
        className="my-repos__token-form"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <TextInput
          type="password"
          value={token}
          onUpdate={setToken}
          placeholder={demo ? "В демо токен не нужен" : "Токен SourceCraft"}
          size="l"
          autoComplete="off"
          className="my-repos__token-input"
          disabled={busy || demo}
        />
        <Button
          view="action"
          size="l"
          type="submit"
          disabled={!demo && token.trim().length === 0}
          loading={busy}
        >
          {demo ? "Подключить демо" : "Подключить"}
        </Button>
      </form>

      {error && (
        <Alert
          theme="danger"
          view="outlined"
          title="Не удалось подключить SourceCraft"
          message={describeError(error)}
        />
      )}

      <Text variant="body-1" color="secondary">
        Токен можно отозвать в SourceCraft в любой момент — тогда мы потеряем доступ, а отчёты по закрытым
        репозиториям перестанут обновляться.
      </Text>
    </section>
  );
}

function ConnectionBar({
  connection,
  onDisconnected,
}: {
  connection: SourceCraftConnection;
  onDisconnected: () => void;
}) {
  const [busy, setBusy] = useState(false);

  return (
    <div className="my-repos__connection">
      <Label theme="success" size="s">
        SourceCraft подключён
      </Label>
      <Text variant="body-1" color="secondary">
        {connection.login ? `как ${connection.login}` : ""}
        {connection.connectedAt ? ` · ${formatDateTimeCompact(connection.connectedAt)}` : ""}
      </Text>
      <Button
        view="flat"
        size="s"
        loading={busy}
        onClick={() => {
          setBusy(true);
          void disconnectSourceCraft()
            .then(onDisconnected)
            .finally(() => setBusy(false));
        }}
      >
        Отключить
      </Button>
    </div>
  );
}

function RepositoryList() {
  const [state, reload] = useAsync(fetchMyRepositories, []);
  const data = dataOf(state);
  const analysis = useStartAnalysis();
  const items = useRecentAnalyses(data?.items);

  if (!data) {
    return state.status === "error" ? (
      <ErrorNote title="Не удалось получить список репозиториев" error={state.error} onRetry={reload} />
    ) : (
      <LoadingNote>Загружаем репозитории</LoadingNote>
    );
  }

  if (data.items.length === 0) {
    return (
      <Text variant="body-2" color="secondary">
        В SourceCraft пока нет репозиториев, к которым у вас есть доступ. Когда появятся, они будут здесь.
      </Text>
    );
  }

  return (
    <>
      <table className="my-repos__table">
        <thead>
          <tr>
            <th scope="col">Репозиторий</th>
            <th scope="col">Последний анализ</th>
            <th scope="col" className="my-repos__score">
              Score
            </th>
            <th scope="col">
              <span className="visually-hidden">Действия</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <RepositoryRow
              key={item.repository.id}
              item={item}
              starting={analysis.startingId === item.repository.id}
              onStart={() => void analysis.start(item.repository.id)}
            />
          ))}
        </tbody>
      </table>
      {analysis.error && (
        <Alert
          className="my-repos__error"
          theme="danger"
          view="outlined"
          title="Не удалось запустить анализ"
          message={describeError(analysis.error)}
        />
      )}
    </>
  );
}

interface RepositoryRowProps {
  item: MyRepository;
  starting: boolean;
  onStart: () => void;
}

function RepositoryRow({ item, starting, onStart }: RepositoryRowProps) {
  const { repository, lastAnalysis, activeAnalysisId } = item;
  const hasReport = lastAnalysis !== null && (lastAnalysis.status === "completed" || lastAnalysis.status === "partial");

  return (
    <tr>
      <td className="my-repos__name">
        <span className="my-repos__title">
          {hasReport ? (
            <Link className="my-repos__link" to={paths.analysis(lastAnalysis.id)}>
              <span className="my-repos__org">{repository.organizationSlug} /</span> {repository.repositorySlug}
            </Link>
          ) : (
            <span className="my-repos__link">
              <span className="my-repos__org">{repository.organizationSlug} /</span> {repository.repositorySlug}
            </span>
          )}
          {repository.visibility === "private" && (
            <Label size="xs" theme="unknown">
              закрытый
            </Label>
          )}
        </span>
        {repository.description && (
          <Text variant="body-1" color="secondary" className="my-repos__about">
            {repository.description}
          </Text>
        )}
      </td>

      <td className="my-repos__last">
        {activeAnalysisId ? (
          <Text variant="body-1" color="info">
            идёт анализ…
          </Text>
        ) : lastAnalysis === null ? (
          <Text variant="body-1" color="secondary">
            {repository.isEmpty ? "пустой — нечего проверять" : "ещё не проверяли"}
          </Text>
        ) : lastAnalysis.status === "failed" || lastAnalysis.status === "cancelled" ? (
          <Text variant="body-1" color="danger">
            {lastAnalysis.status === "failed" ? "не удался" : "отменён"}
            {lastAnalysis.analyzedAt && `, ${formatDateTimeCompact(lastAnalysis.analyzedAt)}`}
          </Text>
        ) : (
          <Text variant="body-1" color="secondary">
            {lastAnalysis.analyzedAt && formatDateTimeCompact(lastAnalysis.analyzedAt)}
          </Text>
        )}
      </td>

      <td
        className={cn(
          "my-repos__score",
          lastAnalysis?.score != null && `my-repos__score_band_${getScoreBand(lastAnalysis.score)}`,
        )}
      >
        {lastAnalysis?.score != null && !activeAnalysisId ? (
          <span className="num">{formatScore(lastAnalysis.score)}</span>
        ) : (
          <Text variant="body-1" color="secondary">
            —
          </Text>
        )}
      </td>

      <td className="my-repos__actions">
        <span className="my-repos__actions-row">
          {activeAnalysisId ? (
            <Button view="outlined" size="m" {...spaLinkProps(paths.analysis(activeAnalysisId))}>
              Смотреть ход
            </Button>
          ) : (
            <>
              {hasReport && (
                <GravityLink {...spaLinkProps(paths.analysis(lastAnalysis.id))} className="my-repos__report-link">
                  Отчёт
                </GravityLink>
              )}
              {/* В пустом репозитории нет ни одного коммита: SourceCraft не отдаст ветку, анализ не запустится. */}
              <Button
                view={hasReport ? "outlined" : "action"}
                size="m"
                loading={starting}
                disabled={repository.isEmpty}
                title={repository.isEmpty ? "В репозитории ещё нет коммитов" : undefined}
                onClick={onStart}
              >
                {lastAnalysis === null ? "Проверить" : "Проверить снова"}
              </Button>
            </>
          )}
        </span>
      </td>
    </tr>
  );
}
