import { Magnifier } from "@gravity-ui/icons";
import { Button, Checkbox, Icon, SegmentedRadioGroup, Select, Text, TextInput } from "@gravity-ui/uikit";
import { useEffect, useState, type MouseEvent } from "react";

import {
  defaultLeaderboardQuery,
  fetchLeaderboard,
  parseLeaderboardQuery,
  stringifyLeaderboardQuery,
  type LeaderboardItem,
  type LeaderboardQuery,
  type LeaderboardResponse,
  type LeaderboardSort,
} from "../api/leaderboard";
import { CategoryCells } from "../components/CategoryCells";
import { ErrorNote, LoadingNote } from "../components/PageNotes";
import { dataOf, useAsync } from "../hooks/useAsync";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { cn } from "../lib/classNames";
import {
  formatDate,
  formatDateTime,
  formatInteger,
  formatLikes,
  formatRelativeDay,
  formatScore,
  formatTime,
  plural,
} from "../lib/format";
import { getScoreBand } from "../lib/scoreBands";
import { Link, navigate, useLocation } from "../router";
import { paths } from "../routes";
import "./LeaderboardPage.css";

const sortOptions: Array<{ value: LeaderboardSort; content: string }> = [
  { value: "score", content: "по Score" },
  { value: "likes", content: "по лайкам" },
  { value: "activity", content: "по активности" },
];

export function LeaderboardPage() {
  useDocumentTitle("Рейтинг здоровья");
  const { search } = useLocation();
  const query = parseLeaderboardQuery(search);
  const [state, reload] = useAsync(() => fetchLeaderboard(query), [search]);
  const data = dataOf(state);

  function update(patch: Partial<LeaderboardQuery>): void {
    // Любая смена фильтра возвращает на первую страницу.
    const next: LeaderboardQuery = { ...query, page: 1, ...patch };
    navigate(`${paths.leaderboard()}${stringifyLeaderboardQuery(next)}`, { replace: true, keepScroll: true });
  }

  return (
    <div className="page__inner">
      <div className="page__header">
        <div>
          <Text variant="header-2" as="h1" className="page__title">
            Рейтинг здоровья
          </Text>
          <Text variant="body-2" color="secondary" className="leaderboard__about">
            Оценка от 0 до 100 показывает, насколько репозиторием удобно и безопасно пользоваться: собирается ли
            он сам, отвечают ли авторам задач, есть ли понятное описание.
          </Text>
          {data && (
            <Text variant="body-2" color="secondary">
              {describeCoverage(data)}
            </Text>
          )}
        </div>
        <Text variant="body-1" color="secondary" className="leaderboard__principle">
          Место определяет только Repo Health Score. Сортировка по лайкам или активности меняет порядок строк, но не
          места.
        </Text>
      </div>

      <div className="leaderboard__controls">
        <SearchField value={query.search} onChange={(value) => update({ search: value })} />
        <Select
          value={query.language ? [query.language] : []}
          onUpdate={(value) => update({ language: value[0] ?? null })}
          placeholder="Все языки"
          width={200}
          hasClear
          options={(data?.languages ?? []).map((facet) => ({
            value: facet.name,
            content: `${facet.name} · ${facet.count}`,
          }))}
        />
        <SegmentedRadioGroup
          value={query.sort}
          onUpdate={(value) => update({ sort: value as LeaderboardSort })}
          options={sortOptions}
          aria-label="Сортировка"
        />
        <Checkbox
          checked={query.includePreliminary}
          onUpdate={(checked) => update({ includePreliminary: checked })}
          content="Показать предварительные"
          className="leaderboard__preliminary-toggle"
        />
      </div>

      {state.status === "error" && (
        <ErrorNote title="Не удалось загрузить рейтинг" error={state.error} onRetry={reload} />
      )}
      {!data && state.status === "loading" && <LoadingNote>Загружаем рейтинг</LoadingNote>}

      {data && (
        <>
          <LeaderboardTable
            items={data.items}
            loading={state.status === "loading"}
            showPlaces
            emptyText="Под эти фильтры ничего не подошло."
            onReset={() => update(defaultLeaderboardQuery)}
          />

          {data.total > data.pageSize && (
            <nav className="pager" aria-label="Страницы рейтинга">
              <Button view="outlined" disabled={data.page <= 1} onClick={() => update({ page: data.page - 1 })}>
                ← Назад
              </Button>
              <Text variant="body-1" color="secondary" className="num">
                {(data.page - 1) * data.pageSize + 1}–{(data.page - 1) * data.pageSize + data.items.length} из{" "}
                {data.total}
              </Text>
              <Button
                view="outlined"
                disabled={(data.page - 1) * data.pageSize + data.items.length >= data.total}
                onClick={() => update({ page: data.page + 1 })}
              >
                Дальше →
              </Button>
            </nav>
          )}

          <Legend />

          {query.includePreliminary && (
            <section className="section leaderboard__preliminary">
              <div className="section__head">
                <Text variant="subheader-2" as="h2">
                  Предварительные оценки
                </Text>
                <Text variant="body-1" color="secondary">
                  данные есть не по всем категориям, поэтому места в рейтинге у них нет
                </Text>
              </div>
              <LeaderboardTable
                items={data.preliminary}
                loading={state.status === "loading"}
                showPlaces={false}
                emptyText="Предварительных оценок под эти фильтры нет."
              />
            </section>
          )}
        </>
      )}
    </div>
  );
}

interface LeaderboardTableProps {
  items: LeaderboardItem[];
  loading: boolean;
  showPlaces: boolean;
  emptyText: string;
  onReset?: () => void;
}

function LeaderboardTable({ items, loading, showPlaces, emptyText, onReset }: LeaderboardTableProps) {
  if (items.length === 0) {
    return (
      <div className="leaderboard__empty">
        <Text variant="body-2" color="secondary">
          {emptyText}
        </Text>
        {onReset && (
          <Button view="outlined" onClick={onReset}>
            Сбросить фильтры
          </Button>
        )}
      </div>
    );
  }

  return (
    <table className={cn("board", loading && "board_loading")} aria-busy={loading}>
      <thead>
        <tr>
          {showPlaces && (
            <th scope="col" className="board__place">
              Место
            </th>
          )}
          <th scope="col">Репозиторий</th>
          <th scope="col" className="board__cells" title="Слева направо: безопасность, CI/CD, документация, активность, работа с задачами, состояние кода">
            Из чего оценка
          </th>
          <th scope="col" className="board__score">
            Score
          </th>
          <th scope="col" className="board__likes">
            Лайки
          </th>
          <th scope="col" className="board__language">
            Язык
          </th>
          <th scope="col" className="board__activity">
            Активность
          </th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <LeaderboardRow key={item.repository.id} item={item} showPlace={showPlaces} />
        ))}
      </tbody>
    </table>
  );
}

function LeaderboardRow({ item, showPlace }: { item: LeaderboardItem; showPlace: boolean }) {
  const { repository } = item;
  const reportPath = paths.analysis(item.analysisId);

  // Кликабельна вся строка; с клавиатуры переходят по ссылке в названии.
  function openReport(event: MouseEvent<HTMLTableRowElement>): void {
    if ((event.target as HTMLElement).closest("a, button")) return;
    navigate(reportPath);
  }

  return (
    <tr className="board__row" onClick={openReport}>
      {showPlace && (
        <td className="board__place num">
          {item.place === null ? (
            "—"
          ) : item.place <= 3 ? (
            <span className={cn("board__medal", item.place > 1 && `board__medal_place_${item.place}`)}>{item.place}</span>
          ) : (
            item.place
          )}
        </td>
      )}
      <td className="board__name">
        <Link className="board__link" to={reportPath}>
          <span className="board__org">{repository.organizationSlug} /</span> {repository.repositorySlug}
        </Link>
        {repository.description && (
          <Text variant="body-1" color="secondary" className="board__about">
            {repository.description}
          </Text>
        )}
      </td>
      <td className="board__cells">
        <CategoryCells categories={item.categories} />
      </td>
      <td className={cn("board__score", item.score !== null && `board__score_band_${getScoreBand(item.score)}`)}>
        {item.score === null ? (
          <Text variant="body-1" color="secondary">
            нет оценки
          </Text>
        ) : (
          <>
            <span className="board__score-value">{formatScore(item.score)}</span>
            {item.scoreLimited && (
              <span className="board__mark" title="Score ограничен из-за критической проблемы">
                !
              </span>
            )}
          </>
        )}
      </td>
      <td className="board__likes num">{item.likes === null ? "—" : formatLikes(item.likes)}</td>
      <td className="board__language">{repository.language ?? "—"}</td>
      <td className="board__activity">
        {item.lastActivityAt ? (
          <time dateTime={item.lastActivityAt} title={formatDate(item.lastActivityAt)}>
            {formatRelativeDay(item.lastActivityAt)}
          </time>
        ) : (
          "—"
        )}
      </td>
    </tr>
  );
}

function Legend() {
  return (
    <div className="leaderboard__legend">
      <Text variant="body-1" color="secondary">
        Клетка — часть проекта, слева направо: безопасность, сборка, описание, активность, работа с задачами, состояние кода
      </Text>
      <span className="leaderboard__legend-item">
        <i className="leaderboard__swatch leaderboard__swatch_band_high" /> 80 и выше
      </span>
      <span className="leaderboard__legend-item">
        <i className="leaderboard__swatch leaderboard__swatch_band_mid" /> 60–79
      </span>
      <span className="leaderboard__legend-item">
        <i className="leaderboard__swatch leaderboard__swatch_band_low" /> ниже 60
      </span>
      <span className="leaderboard__legend-item">
        <i className="leaderboard__swatch leaderboard__swatch_status_none" /> нет данных
      </span>
      <span className="leaderboard__legend-item">
        <i className="leaderboard__swatch leaderboard__swatch_status_na" /> не применимо
      </span>
      <span className="leaderboard__legend-item">
        <b className="board__mark">!</b> оценка ограничена
      </span>
    </div>
  );
}

function SearchField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [draft, setDraft] = useState(value);

  useEffect(() => setDraft(value), [value]);

  // Ждём паузу в наборе, чтобы не дёргать backend на каждую букву.
  useEffect(() => {
    if (draft.trim() === value) return;
    const timer = window.setTimeout(() => onChange(draft.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [draft]);

  return (
    <TextInput
      value={draft}
      onUpdate={setDraft}
      placeholder="Организация или репозиторий"
      startContent={<Icon data={Magnifier} size={16} className="leaderboard__search-icon" />}
      hasClear
      className="leaderboard__search"
    />
  );
}

function describeCoverage(data: LeaderboardResponse): string {
  const parts = [`${formatInteger(data.total)} ${plural(data.total, "репозиторий", "репозитория", "репозиториев")}`];
  if (data.updatedAt) {
    const relative = formatRelativeDay(data.updatedAt);
    parts.push(
      relative === "сегодня" || relative === "вчера"
        ? `пересчитан ${relative} в ${formatTime(data.updatedAt)}`
        : `пересчитан ${formatDateTime(data.updatedAt)}`,
    );
  }
  if (data.pendingCount > 0) {
    parts.push(
      `ещё ${formatInteger(data.pendingCount)} ${plural(data.pendingCount, "ждёт", "ждут", "ждут")} первого анализа`,
    );
  }
  return parts.join(" · ");
}
