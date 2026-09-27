import { Alert, Button, Label, Link as GravityLink, Text, TextInput } from "@gravity-ui/uikit";
import { useState } from "react";

import { describeStartError } from "../api/analyses";
import {
  connectSourceCraft,
  disconnectSourceCraft,
  fetchSourceCraftConnection,
  type SourceCraftConnection,
} from "../api/connections";
import { describeError } from "../api/http";
import { fetchMyRepositories, type MyRepository } from "../api/me";
import { useAuth } from "../auth/AuthContext";
import { ErrorNote, LoadingNote } from "../components/PageNotes";
import { dataOf, useAsync } from "../hooks/useAsync";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useRecentAnalyses } from "../hooks/useRecentAnalyses";
import { useStartAnalysis } from "../hooks/useStartAnalysis";
import { cn } from "../lib/classNames";
import { yandexAuthPendingHint } from "../lib/featureFlags";
import { formatDateTimeCompact, formatScore } from "../lib/format";
import { getScoreBand } from "../lib/scoreBands";
import { Link } from "../router";
import { paths } from "../routes";
import "./MyRepositoriesPage.css";

export function MyRepositoriesPage() {
  useDocumentTitle("Мои репозитории");
  const auth = useAuth();

  return (
    <div className="page__inner">
      <div className="page__header">
        <div>
          <Text variant="header-2" as="h1" className="page__title">
            Мои репозитории
          </Text>
          <Text variant="body-2" color="secondary" className="page__lead">
            Репозитории SourceCraft, которые можно проверить: выберите нужный и запустите анализ.
          </Text>
        </div>
      </div>

      {auth.status === "unknown" && <LoadingNote>Проверяем вход</LoadingNote>}
      {auth.status === "guest" && (
        <SignInInvite unavailable={!auth.canSignIn} onSignIn={() => auth.signIn(paths.myRepositories())} />
      )}
      {auth.status === "signedIn" && <ConnectedArea />}
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
        Яндекс ID подтверждает, кто вы. После входа появится список репозиториев SourceCraft, которые можно
        проверить.
      </Text>
      <ol className="my-repos__steps">
        <li>Войдите через Яндекс ID.</li>
        <li>Выберите репозиторий из списка и запустите проверку.</li>
        <li>Через пару минут получите оценку, объяснение и список действий.</li>
      </ol>
      <div className="my-repos__invite-actions">
        <Button view="action" size="l" disabled={unavailable} onClick={onSignIn}>
          Войти через Яндекс ID
        </Button>
        {unavailable && (
          <Text variant="body-1" color="secondary">
            {yandexAuthPendingHint}.
          </Text>
        )}
      </div>
    </section>
  );
}

/*
 * Сначала — список: на первом этапе это публичные репозитории из каталога сервиса, и подключать
 * для них ничего не нужно. Подключение SourceCraft по токену откроет закрытые репозитории; его
 * предлагаем, только если backend его поддерживает, и список им не загораживаем.
 */
function ConnectedArea() {
  const [state, reload] = useAsync(fetchSourceCraftConnection, []);
  const connection = dataOf(state) ?? null;

  return (
    <>
      {connection?.connected && <ConnectionBar connection={connection} onDisconnected={reload} />}
      {connection && !connection.connected && <ConnectForm onConnected={reload} />}
      <RepositoryList key={connection?.connected ? "with-connection" : "catalog"} />
    </>
  );
}

function ConnectForm({ onConnected }: { onConnected: () => void }) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  async function submit(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await connectSourceCraft(token);
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
        Закрытые репозитории — через подключение SourceCraft
      </Text>
      <Text variant="body-2" color="secondary">
        Открытые репозитории ниже проверяются и так. Чтобы проверить закрытые, нужен личный токен SourceCraft: вход
        через Яндекс ID доступа к ним не даёт. Создайте токен в настройках профиля SourceCraft и вставьте сюда — он
        уходит на наш сервер один раз, хранится зашифрованно и обратно в браузер не возвращается.
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
          placeholder="Токен SourceCraft"
          size="l"
          autoComplete="off"
          className="my-repos__token-input"
          disabled={busy}
        />
        <Button view="action" size="l" type="submit" disabled={token.trim().length === 0} loading={busy}>
          Подключить
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
        Пока нет репозиториев, которые можно проверить. Когда появятся, они будут здесь, а готовые отчёты по
        открытым проектам уже есть в <Link to={paths.leaderboard()}>рейтинге</Link>.
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
          message={describeStartError(analysis.error)}
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
            <Button view="outlined" size="m" href={paths.analysis(activeAnalysisId)}>
              Смотреть ход
            </Button>
          ) : (
            <>
              {hasReport && (
                <GravityLink href={paths.analysis(lastAnalysis.id)} className="my-repos__report-link">
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
