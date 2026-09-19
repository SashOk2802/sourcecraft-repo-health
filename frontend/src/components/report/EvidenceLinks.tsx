import { Link as GravityLink, Text } from "@gravity-ui/uikit";
import { useState } from "react";

import type { Evidence } from "../../api/common";
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
      {shown.map((item) => (
        <li className="evidence__item" key={`${item.source}:${item.reference}`}>
          {item.url ? (
            <GravityLink href={item.url} target="_blank" rel="noreferrer" className="evidence__ref">
              {item.reference}
            </GravityLink>
          ) : (
            <span className="evidence__ref">{item.reference}</span>
          )}
          {item.summary && (
            <Text variant="body-1" color="secondary">
              {" "}
              — {item.summary}
            </Text>
          )}
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
