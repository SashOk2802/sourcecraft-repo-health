import { useEffect } from "react";

export interface PageMeta {
  /** Заголовок вкладки без названия сервиса; пустой — только название. */
  title: string;
  /** Описание для поиска и превью ссылок; без него — общее из index.html. */
  description?: string;
  /**
   * Не индексировать: личный кабинет, демо-данные, ход анализа, 404.
   * Поисковику не нужны ни чужие закрытые данные, ни вымышленные репозитории.
   */
  noindex?: boolean;
}

const SITE_NAME = "Repo Health";

// Описание из index.html — возвращаем его на страницах без своего.
const defaultDescription =
  typeof document === "undefined" ? "" : (document.querySelector('meta[name="description"]')?.getAttribute("content") ?? "");

export function usePageMeta({ title, description, noindex = false }: PageMeta): void {
  useEffect(() => {
    document.title = title ? `${title} — ${SITE_NAME}` : `${SITE_NAME} — здоровье репозиториев SourceCraft`;
    setMeta("description", description ?? defaultDescription);
    setMeta("robots", noindex ? "noindex" : null);
    // Фильтры рейтинга живут в адресе: канонический адрес страницы — без них.
    setCanonical(`${window.location.origin}${window.location.pathname}`);
  }, [title, description, noindex]);
}

function setMeta(name: string, content: string | null): void {
  let element = document.head.querySelector<HTMLMetaElement>(`meta[name="${name}"]`);
  if (content === null) {
    element?.remove();
    return;
  }
  if (!element) {
    element = document.createElement("meta");
    element.name = name;
    document.head.append(element);
  }
  element.content = content;
}

function setCanonical(href: string): void {
  let link = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]');
  if (!link) {
    link = document.createElement("link");
    link.rel = "canonical";
    document.head.append(link);
  }
  link.href = href;
}
