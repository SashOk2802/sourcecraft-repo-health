import { Pulse } from "@gravity-ui/icons";
import { Button, Icon, Text } from "@gravity-ui/uikit";
import { useState } from "react";

import { signInUnavailableHint, useAuth } from "../auth/AuthContext";
import { cn } from "../lib/classNames";
import { useReportSection } from "../lib/reportSection";
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
  { label: "Рейтинг", to: paths.leaderboard(), pages: ["leaderboard"] },
  { label: "Мои репозитории", to: paths.myRepositories(), pages: ["myRepositories"] },
  { label: "Как считаем", to: paths.methodology(), pages: ["methodology"] },
  { label: "API", to: paths.publicApi(), pages: ["publicApi"] },
];

export function SiteHeader({ route }: { route: Route }) {
  // Отчёт закрытого репозитория — в «Моих репозиториях», публичного — в рейтинге: раздел знает страница анализа.
  const reportSection = useReportSection();
  const page = route.page === "analysis" ? reportSection : route.page;

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
            const active = page !== null && item.pages.includes(page);
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
          <UserArea />
        </div>
      </div>
    </header>
  );
}

function UserArea() {
  const auth = useAuth();
  const [signOutFailed, setSignOutFailed] = useState(false);

  async function handleSignOut(): Promise<void> {
    setSignOutFailed(false);
    try {
      await auth.signOut();
    } catch {
      // Сессию в интерфейсе не меняем: пользователь может повторить выход.
      setSignOutFailed(true);
    }
  }

  if (auth.status === "unknown") {
    return null;
  }

  if (auth.user) {
    return (
      <>
        <Text variant="body-2" color="secondary" className="site-header__user-name">
          {auth.user.displayName}
        </Text>
        {auth.mode === "demo" && <DemoMark />}
        <Button view="flat" size="m" onClick={() => void handleSignOut()}>
          Выйти
        </Button>
        {signOutFailed && (
          <Text variant="caption-2" color="danger" role="status">
            Не удалось выйти. Попробуйте ещё раз.
          </Text>
        )}
      </>
    );
  }

  // Режим api, а вход на backend не настроен или он не отвечает: кнопка выключена, чтобы не вести на 503.
  if (auth.mode === "offline") {
    return (
      <span className="site-header__signin" title={signInUnavailableHint}>
        <Button view="outlined" size="m" disabled>
          Войти<span className="site-header__signin-long"> через Яндекс ID</span>
        </Button>
        <Text variant="caption-2" color="secondary" className="site-header__signin-note">
          недоступен
        </Text>
      </span>
    );
  }

  return (
    <span className="site-header__signin">
      <Button view="outlined" size="m" onClick={auth.signIn}>
        Войти<span className="site-header__signin-long"> через Яндекс ID</span>
      </Button>
      {auth.mode === "demo" && <DemoMark />}
    </span>
  );
}

/** Вход в демо-кабинет: настоящего Яндекс ID за ним пока нет, и это видно. */
function DemoMark() {
  return (
    <Text
      variant="caption-2"
      color="secondary"
      className="site-header__signin-note"
      title="Демо-кабинет: вход и репозитории показаны на примере. Настоящий вход через Яндекс ID включится, когда на сервере настроят OAuth."
    >
      демо
    </Text>
  );
}
