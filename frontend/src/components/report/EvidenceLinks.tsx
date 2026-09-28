import { Link as GravityLink, Text } from "@gravity-ui/uikit";
import { useState } from "react";

import type { Evidence } from "../../api/common";
import { evidenceReferenceText, evidenceSummaryText, isTechnicalReference, sourceLink } from "./reportHelpers";
import "./EvidenceLinks.css";

interface EvidenceLinksProps {
  items: Evidence[];
  /** Сколько фактов показать до кнопки «ещё». */
  limit?: number;
}

/** Подтверждающие факты: прогоны CI, задачи, файлы, находки AppSec. */
export function EvidenceLinks({ items, limit = 3 }: EvidenceLinksProps) {
  const [expanded, setExpanded] = useState(false);

  if (items.length === 0) {
    return null;
  }

  const shown = expanded ? items : items.slice(0, limit);
  const hiddenCount = items.length - shown.length;

  return (
    <ul className="evidence">
      {/* Ссылки могут повторяться: TODO и FIXME на одной строке файла дают одинаковый «путь:строка». */}
      {shown.map((item, index) => (
        <li className="evidence__item" key={`${index}:${item.source}:${item.reference}`}>
          <EvidenceItem item={item} />
        </li>
      ))}
      {hiddenCount > 0 && (
        <li className="evidence__item">
          <button className="evidence__more" type="button" onClick={() => setExpanded(true)}>
            и ещё {hiddenCount}
          </button>
        </li>
      )}
    </ul>
  );
}

function EvidenceItem({ item }: { item: Evidence }) {
  // Страница SourceCraft — короткой подписью, а длинное описание backend — подсказкой.
  const page = sourceLink(item.reference);
  if (page && item.url) {
    return (
      <>
        <GravityLink
          href={item.url}
          target="_blank"
          rel="noreferrer"
          className="evidence__ref"
          title={item.summary || undefined}
        >
          {page.label}
        </GravityLink>
        {page.note && (
          <Text variant="body-1" color="secondary">
            {" "}
            — {page.note}
          </Text>
        )}
      </>
    );
  }

  // Служебный код вроде last_updated заменяем описанием: ссылка остаётся, пропадает только код.
  if (isTechnicalReference(item.reference) && item.summary) {
    return item.url ? (
      <GravityLink href={item.url} target="_blank" rel="noreferrer" className="evidence__ref">
        {item.summary}
      </GravityLink>
    ) : (
      <Text variant="body-1" color="secondary">
        {item.summary}
      </Text>
    );
  }

  const reference = evidenceReferenceText(item.reference);
  const summary = evidenceSummaryText(item);
  return (
    <>
      {item.url ? (
        <GravityLink href={item.url} target="_blank" rel="noreferrer" className="evidence__ref">
          {reference}
        </GravityLink>
      ) : (
        <span className="evidence__ref">{reference}</span>
      )}
      {summary && (
        <Text variant="body-1" color="secondary">
          {" "}
          — {summary}
        </Text>
      )}
    </>
  );
}
