import { Text } from "@gravity-ui/uikit";

import type { GamingWarning } from "../../api/report";

/**
 * Предупреждение детектора накрутки. Рендерить только когда поле есть в отчёте
 * и suspected=true; старые снимки без gamingWarning ничего не показывают.
 * Отсутствие данных не выдаётся за «накрутки нет».
 */
export function GamingWarningBanner({ warning }: { warning: GamingWarning | undefined }) {
  if (!warning?.suspected) {
    return null;
  }

  const flagged = warning.signals.filter((signal) => signal.flagged);

  return (
    <section className="gaming-warning" aria-label="Предупреждение о накрутке">
      <Text variant="subheader-2" as="h2" className="gaming-warning__title">
        {warning.label ?? "есть признаки накрутки"}
      </Text>
      <Text variant="body-2" color="secondary">
        {warning.summary}
      </Text>
      <Text variant="body-2" color="secondary">
        Сигнал не меняет Repo Health Score и место в рейтинге.
      </Text>
      {flagged.length > 0 && (
        <ul className="gaming-warning__signals">
          {flagged.map((signal) => (
            <li key={signal.code}>
              <Text variant="body-2">{signal.summary}</Text>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
