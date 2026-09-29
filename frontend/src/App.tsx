import { lazy, Suspense, type ReactElement } from "react";

import { AuthProvider } from "./auth/AuthContext";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { SiteFooter } from "./components/SiteFooter";
import { SiteHeader } from "./components/SiteHeader";
import { useLocation, useRoute } from "./router";
import { ThemeChoiceProvider } from "./theme/ThemeChoice";
import type { Route } from "./routes";

// Страницы отчёта и рейтинга содержат графики и таблицы. Их не нужно скачивать
// до перехода по соответствующему маршруту: Vite выделяет каждый import в chunk.
const AnalysisPage = lazy(() =>
  import("./pages/AnalysisPage").then((module) => ({ default: module.AnalysisPage })),
);
const LeaderboardPage = lazy(() =>
  import("./pages/LeaderboardPage").then((module) => ({ default: module.LeaderboardPage })),
);
const MethodologyPage = lazy(() =>
  import("./pages/MethodologyPage").then((module) => ({ default: module.MethodologyPage })),
);
const MyRepositoriesPage = lazy(() =>
  import("./pages/MyRepositoriesPage").then((module) => ({ default: module.MyRepositoriesPage })),
);
const NotFoundPage = lazy(() =>
  import("./pages/NotFoundPage").then((module) => ({ default: module.NotFoundPage })),
);

export function App() {
  const route = useRoute();
  const { pathname } = useLocation();

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

function PageLoading(): ReactElement {
  return (
    <p className="page__inner" aria-live="polite">
      Загружаем страницу…
    </p>
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
