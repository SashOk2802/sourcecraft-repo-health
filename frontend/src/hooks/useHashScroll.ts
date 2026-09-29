import { useEffect } from "react";

/**
 * Страницу открыли по ссылке с якорем, например /methodology#no-data. Браузер ищет раздел
 * сразу, пока его ещё нет — страница грузится частями и ждёт данных, — и остаётся наверху.
 * Когда разделы отрисованы (ready), докручиваем сами: мгновенно, это вход на страницу,
 * а не переход внутри неё.
 */
export function useHashScroll(ready: boolean): void {
  useEffect(() => {
    if (!ready) return;
    const id = hashTarget(window.location.hash);
    if (id) document.getElementById(id)?.scrollIntoView({ block: "start", behavior: "instant" });
  }, [ready]);
}

/** id раздела из #якоря; битый %-код не роняет страницу. */
export function hashTarget(hash: string): string | null {
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  if (!raw) return null;
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}
