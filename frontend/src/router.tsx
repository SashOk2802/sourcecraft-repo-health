import { useEffect, useState, type AnchorHTMLAttributes, type MouseEvent } from "react";

import { matchRoute, type Route } from "./routes";

// Своё событие: pushState не вызывает popstate, а подписчикам нужно узнать о переходе.
const NAVIGATION_EVENT = "repo-health:navigate";

interface NavigateOptions {
  /** Заменить текущую запись истории, например при смене фильтров. */
  replace?: boolean;
  /** Не прокручивать страницу наверх. */
  keepScroll?: boolean;
}

export function navigate(to: string, { replace = false, keepScroll = false }: NavigateOptions = {}): void {
  if (replace) {
    window.history.replaceState(null, "", to);
  } else {
    window.history.pushState(null, "", to);
  }
  window.dispatchEvent(new Event(NAVIGATION_EVENT));
  if (!keepScroll) {
    window.scrollTo(0, 0);
  }
}

export interface LocationState {
  pathname: string;
  search: string;
}

export function useLocation(): LocationState {
  const [location, setLocation] = useState<LocationState>(currentLocation);

  useEffect(() => {
    const update = (): void => setLocation(currentLocation());
    window.addEventListener("popstate", update);
    window.addEventListener(NAVIGATION_EVENT, update);
    return () => {
      window.removeEventListener("popstate", update);
      window.removeEventListener(NAVIGATION_EVENT, update);
    };
  }, []);

  return location;
}

export function useRoute(): Route {
  return matchRoute(useLocation().pathname);
}

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & { to: string };

/** Ссылка внутри приложения: без перезагрузки страницы, но с обычным href для новой вкладки. */
export function Link({ to, onClick, target, ...rest }: LinkProps) {
  function handleClick(event: MouseEvent<HTMLAnchorElement>): void {
    onClick?.(event);
    const opensElsewhere =
      event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || target === "_blank";
    if (event.defaultPrevented || opensElsewhere) {
      return;
    }
    event.preventDefault();
    navigate(to);
  }

  return <a {...rest} href={to} target={target} onClick={handleClick} />;
}

/**
 * Для кнопок-ссылок Gravity UI: обычный href (новая вкладка, копирование адреса)
 * и переход без перезагрузки по простому клику — как у Link.
 */
export function spaLinkProps(to: string): { href: string; onClick: (event: MouseEvent<HTMLElement>) => void } {
  return {
    href: to,
    onClick: (event) => {
      const opensElsewhere = event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey;
      if (event.defaultPrevented || opensElsewhere) {
        return;
      }
      event.preventDefault();
      navigate(to);
    },
  };
}

/**
 * Адрес прямо сейчас. Обработчик, созданный на прошлом рендере (например, отложенный поиск),
 * берёт текущие фильтры отсюда, а не из своего замыкания.
 */
export function currentLocation(): LocationState {
  return { pathname: window.location.pathname, search: window.location.search };
}
