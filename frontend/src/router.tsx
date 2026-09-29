import { useEffect, useState, type AnchorHTMLAttributes, type MouseEvent } from "react";

import { matchRoute, type PageName, type Route } from "./routes";

// Своё событие: pushState не вызывает popstate, а подписчикам нужно узнать о переходе.
const NAVIGATION_EVENT = "repo-health:navigate";

interface NavigateOptions {
  /** Заменить текущую запись истории, например при смене фильтров. */
  replace?: boolean;
  /** Не прокручивать страницу наверх. */
  keepScroll?: boolean;
  /** С какой страницы перешли: хранится в записи истории и переживает перезагрузку. */
  from?: PageName;
}

export function navigate(to: string, { replace = false, keepScroll = false, from }: NavigateOptions = {}): void {
  const state = from ? { from } : null;
  if (replace) {
    window.history.replaceState(state, "", to);
  } else {
    window.history.pushState(state, "", to);
  }
  window.dispatchEvent(new Event(NAVIGATION_EVENT));
  if (!keepScroll) {
    window.scrollTo(0, 0);
  }
}

/** С какой страницы пришли на текущую (navigate с from); null — открыли по адресу. */
export function navigationOrigin(): PageName | null {
  const state: unknown = window.history.state;
  if (typeof state === "object" && state !== null && "from" in state && typeof state.from === "string") {
    return state.from as PageName;
  }
  return null;
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

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & { to: string; from?: PageName };

/** Ссылка внутри приложения: без перезагрузки страницы, но с обычным href для новой вкладки. */
export function Link({ to, from, onClick, target, ...rest }: LinkProps) {
  function handleClick(event: MouseEvent<HTMLAnchorElement>): void {
    onClick?.(event);
    const opensElsewhere =
      event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || target === "_blank";
    if (event.defaultPrevented || opensElsewhere) {
      return;
    }
    event.preventDefault();
    navigate(to, { from });
  }

  return <a {...rest} href={to} target={target} onClick={handleClick} />;
}

/**
 * Для кнопок-ссылок Gravity UI: обычный href (новая вкладка, копирование адреса)
 * и переход без перезагрузки по простому клику — как у Link.
 */
export function spaLinkProps(
  to: string,
  from?: PageName,
): { href: string; onClick: (event: MouseEvent<HTMLElement>) => void } {
  return {
    href: to,
    onClick: (event) => {
      const opensElsewhere = event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey;
      if (event.defaultPrevented || opensElsewhere) {
        return;
      }
      event.preventDefault();
      navigate(to, { from });
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
