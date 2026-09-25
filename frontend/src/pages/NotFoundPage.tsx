import { Button, Text } from "@gravity-ui/uikit";

import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { navigate } from "../router";
import { paths } from "../routes";
import "./NotFoundPage.css";

export function NotFoundPage() {
  useDocumentTitle("Страница не найдена");

  return (
    <div className="page__inner">
      <section className="card not-found">
        <Text variant="body-1" color="secondary" className="num">
          404
        </Text>
        <Text variant="header-2" as="h1">
          Такой страницы нет
        </Text>
        <Text variant="body-2" color="secondary">
          Возможно, ссылка устарела или в адресе опечатка.
        </Text>
        <div>
          <Button view="action" size="l" onClick={() => navigate(paths.leaderboard())}>
            К рейтингу
          </Button>
        </div>
      </section>
    </div>
  );
}
