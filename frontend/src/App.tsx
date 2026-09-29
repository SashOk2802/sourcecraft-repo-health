import { lazy, Suspense, useEffect, type ComponentType, type ReactElement } from "react";

import { AuthProvider } from "./auth/AuthContext";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { LoadingNote } from "./components/PageNotes";
import { SiteFooter } from "./components/SiteFooter";
import { SiteHeader } from "./components/SiteHeader";
import { useLocation, useRoute } from "./router";
import { ThemeChoiceProvider } from "./theme/ThemeChoice";
import type { Route } from "./routes";

/*
 * Страница, которую Vite выделяет в отдельный chunk и скачивает при первом переходе.
 * preload скачивает её заранее; lazy получает тот же промис и второй раз не ждёт.
 * После сбоя сети промис сбрасывается, чтобы следующий переход попробовал снова.
 */
function lazyPage<Props extends object>(load: () => Promise<ComponentType<Props>>) {
  let loading: Promise<{ default: ComponentType<Props> }> | null = null;
  const preload = (): Promise<{ default: ComponentType<Props> }> => {
    loading ??= load().then(
      (component) => ({ default: component }),
      (error: unknown) => {
        loading = null;
        throw error;
      },
    );
    return loading;
  };
  return Object.assign(lazy(preload), { preload });
}

// Страницы отчёта и рейтинга содержат графики и таблицы. Их не нужно скачивать
// до первой отрисовки: Vite выделяет каждый import в chunk.
const AnalysisPage = lazyPage(() => import("./pages/AnalysisPage").then((module) => module.AnalysisPage));
const LeaderboardPage = lazyPage(() => import("./pages/LeaderboardPage").then((module) => module.LeaderboardPage));
const MethodologyPage = lazyPage(() => import("./pages/MethodologyPage").then((module) => module.MethodologyPage));
const MyRepositoriesPage = lazyPage(() =>
  import("./pages/MyRepositoriesPage").then((module) => module.MyRepositoriesPage),
);
const NotFoundPage = lazyPage(() => import("./pages/NotFoundPage").then((module) => module.NotFoundPage));

export function App() {
  const route = useRoute();
  const { pathname } = useLocation();
  usePreloadedPages();

  return (
    <ThemeChoiceProvider>
      <AuthProvider>
        <SiteHeader route={route} />
        <main className="page">
          {/* Упавшая страница не роняет шапку; при переходе граница начинает заново.
              Ловит и сбой загрузки части страницы, если сеть оборвалась. */}
          <ErrorBoundary key={pathname}>
            <Suspense fallback={<PageLoading />}>{renderPage(route)}</Suspense>
          </ErrorBoundary>
        </main>
        <SiteFooter />
      </AuthProvider>
    </ThemeChoiceProvider>
  );
}

/*
 * Вкладки шапки открываются сразу: когда первая страница отрисована и браузер свободен,
 * остальные страницы скачиваются заранее. Без этого переход в «Как считаем» ждал
 * загрузки кода, а на стенде с dev-сервером это несколько секунд пустого экрана.
 */
function usePreloadedPages(): void {
  useEffect(() => {
    const preloadAll = (): void => {
      for (const page of [LeaderboardPage, MethodologyPage, MyRepositoriesPage, AnalysisPage]) {
        // Не вышло — не страшно: страница скачается при переходе, как без предзагрузки.
        page.preload().catch(() => undefined);
      }
    };
    // В Safari нет requestIdleCallback: там просто небольшая пауза после загрузки.
    if (typeof window.requestIdleCallback === "function") {
      const handle = window.requestIdleCallback(preloadAll, { timeout: 3000 });
      return () => window.cancelIdleCallback(handle);
    }
    const handle = window.setTimeout(preloadAll, 1500);
    return () => window.clearTimeout(handle);
  }, []);
}

/** Появляется с задержкой (.page-loading): если страница уже скачана, надпись не мигает. */
function PageLoading(): ReactElement {
  return (
    <div className="page__inner page-loading">
      <LoadingNote>Загружаем страницу</LoadingNote>
    </div>
  );
}

function renderPage(route: Route): ReactElement {
  switch (route.page) {
    case "leaderboard":
      return <LeaderboardPage />;
    case "analysis":
      return <AnalysisPage key={route.analysisId} analysisId={route.analysisId} />;
    case "myRepositories":
      return <MyRepositoriesPage />;
    case "methodology":
      return <MethodologyPage />;
    case "notFound":
      return <NotFoundPage />;
  }
}
