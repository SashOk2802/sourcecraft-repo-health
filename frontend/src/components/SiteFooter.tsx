import { Text } from "@gravity-ui/uikit";

import { Link } from "../router";
import { paths } from "../routes";
import "./SiteFooter.css";

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <Text variant="body-1" color="secondary" className="site-footer__text">
        Оценка строится только на данных SourceCraft: истории Git, CI, issues, merge requests и AppSec. Лайки на
        неё не влияют.
      </Text>
      <Link className="site-footer__link" to={paths.methodology()}>
        Как считаем
      </Link>
    </footer>
  );
}
