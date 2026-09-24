import { Text } from "@gravity-ui/uikit";

import { useDocumentTitle } from "../hooks/useDocumentTitle";

/** Временная заглушка для разделов, которые ещё верстаются в отдельных ветках. */
export function WorkInProgress({ title }: { title: string }) {
  useDocumentTitle(title);

  return (
    <div className="page__inner">
      <section className="card" style={{ marginTop: 24 }}>
        <Text variant="header-2" as="h1">
          {title}
        </Text>
        <Text variant="body-2" color="secondary">
          Раздел ещё верстается.
        </Text>
      </section>
    </div>
  );
}
