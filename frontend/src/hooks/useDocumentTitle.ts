import { useEffect } from "react";

export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title ? `${title} — Repo Health` : "Repo Health для SourceCraft";
  }, [title]);
}
