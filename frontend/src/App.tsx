import type { ReactElement } from "react";

import { SiteFooter } from "./components/SiteFooter";
import { SiteHeader } from "./components/SiteHeader";
import { AnalysisPage } from "./pages/AnalysisPage";
import { LeaderboardPage } from "./pages/LeaderboardPage";
import { MethodologyPage } from "./pages/MethodologyPage";
import { MyRepositoriesPage } from "./pages/MyRepositoriesPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { ReportPage } from "./pages/ReportPage";
import { useRoute } from "./router";
import { ThemeChoiceProvider } from "./theme/ThemeChoice";
import type { Route } from "./routes";

export function App() {
  const route = useRoute();

  return (
    <ThemeChoiceProvider>
      <SiteHeader route={route} />
      <main className="page">{renderPage(route)}</main>
      <SiteFooter />
    </ThemeChoiceProvider>
  );
}

function renderPage(route: Route): ReactElement {
  switch (route.page) {
    case "leaderboard":
      return <LeaderboardPage />;
    case "report":
      return (
        <ReportPage
          key={`${route.organizationSlug}/${route.repositorySlug}`}
          organizationSlug={route.organizationSlug}
          repositorySlug={route.repositorySlug}
        />
      );
    case "myRepositories":
      return <MyRepositoriesPage />;
    case "analysis":
      return <AnalysisPage key={route.analysisId} analysisId={route.analysisId} />;
    case "methodology":
      return <MethodologyPage />;
    case "notFound":
      return <NotFoundPage />;
  }
}
