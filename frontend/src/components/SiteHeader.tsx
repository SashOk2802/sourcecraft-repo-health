import { Pulse } from "@gravity-ui/icons";
import { Button, Icon, Text } from "@gravity-ui/uikit";

import { cn } from "../lib/classNames";
import { yandexAuthPendingHint, yandexAuthReady } from "../lib/featureFlags";
import { Link } from "../router";
import { paths, type PageName, type Route } from "../routes";
import { ThemeSwitch } from "./ThemeSwitch";
import "./SiteHeader.css";

interface NavItem {
  label: string;
  to: string;
  /** На каких страницах пункт считается текущим. */
  pages: PageName[];
}

const navItems: NavItem[] = [
  { label: "Рейтинг", to: paths.leaderboard(), pages: ["leaderboard", "analysis"] },
  { label: "Мои репозитории", to: paths.myRepositories(), pages: ["myRepositories"] },
  { label: "Как считаем", to: paths.methodology(), pages: ["methodology"] },
];

export function SiteHeader({ route }: { route: Route }) {
  return (
    <header className="site-header">
      <div className="site-header__inner">
        <Link className="site-header__logo" to={paths.leaderboard()}>
          <span className="site-header__mark">
            <Icon data={Pulse} size={16} />
          </span>
          <Text variant="subheader-2">Repo Health</Text>
          <Text variant="body-1" color="secondary" className="site-header__logo-note">
            для SourceCraft
          </Text>
        </Link>

        <nav className="site-header__nav" aria-label="Разделы">
          {navItems.map((item) => {
            const active = item.pages.includes(route.page);
            return (
              <Link
                key={item.to}
                to={item.to}
                className={cn("site-header__link", active && "site-header__link_active")}
                aria-current={active ? "page" : undefined}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="site-header__user">
          <ThemeSwitch />
          <SignInButton />
        </div>
      </div>
    </header>
  );
}

/*
 * Пока backend не поднял /api/v1/auth/yandex, кнопка показывается выключенной:
 * переход на несуществующий endpoint отдавал 404.
 */
function SignInButton() {
  if (!yandexAuthReady) {
    return (
      <span className="site-header__signin" title={yandexAuthPendingHint}>
        <Button view="outlined" size="m" disabled>
          Войти через Яндекс ID
        </Button>
        <Text variant="caption-2" color="secondary" className="site-header__signin-note">
          скоро
        </Text>
      </span>
    );
  }

  return (
    <Button view="outlined" size="m" href="/api/v1/auth/yandex/start">
      Войти через Яндекс ID
    </Button>
  );
}
