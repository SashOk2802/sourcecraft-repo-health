import { Check, Copy, Shield } from "@gravity-ui/icons";
import { Button, Icon, Modal, Text, TextInput } from "@gravity-ui/uikit";
import { useState } from "react";

import type { RepositoryReport } from "../../api/report";
import "./BadgeSnippet.css";

export function getAppOrigin(): string {
  const envOrigin =
    (typeof import.meta !== "undefined" &&
      (import.meta.env?.VITE_API_BASE_URL || import.meta.env?.VITE_PUBLIC_URL)) ||
    "";
  if (envOrigin) {
    return envOrigin.replace(/\/+$/, "");
  }
  if (typeof window !== "undefined" && window.location?.origin) {
    return window.location.origin;
  }
  return "";
}

export function buildBadgeSnippets(report: RepositoryReport, baseUrl?: string) {
  const { repository, analysis } = report;
  const origin = baseUrl !== undefined ? baseUrl.replace(/\/+$/, "") : getAppOrigin();
  const repoBadgeUrl = `${origin}/api/v1/repositories/${encodeURIComponent(
    repository.organizationSlug,
  )}/${encodeURIComponent(repository.repositorySlug)}/badge.svg`;
  const analysisPageUrl = `${origin}/analyses/${encodeURIComponent(analysis.id)}`;

  const markdownSnippet = `[![Repo Health](${repoBadgeUrl})](${analysisPageUrl})`;
  const htmlSnippet = `<a href="${analysisPageUrl}"><img src="${repoBadgeUrl}" alt="Repo Health"></a>`;

  return {
    repoBadgeUrl,
    analysisPageUrl,
    markdownSnippet,
    htmlSnippet,
  };
}

export interface BadgeSnippetProps {
  report: RepositoryReport;
  defaultOpen?: boolean;
}

export function BadgeSnippet({ report, defaultOpen = false }: BadgeSnippetProps) {
  const [open, setOpen] = useState(defaultOpen);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const { repoBadgeUrl, markdownSnippet, htmlSnippet } = buildBadgeSnippets(report);

  if (!report.badgeAvailable) {
    return (
      <Text variant="body-1" color="secondary" className="badge-snippet__unavailable">
        Бейдж доступен только для публичных репозиториев.
      </Text>
    );
  }

  async function copyToClipboard(text: string, key: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedKey(key);
      setTimeout(() => {
        setCopiedKey((current) => (current === key ? null : current));
      }, 2000);
    } catch {
      // Игнорируем ошибку буфера обмена в неподдерживаемом окружении
    }
  }

  return (
    <>
      <Button view="outlined" onClick={() => setOpen(true)}>
        <Icon data={Shield} size={16} />
        Бейдж для README
      </Button>

      <Modal open={open} onClose={() => setOpen(false)}>
        <div className="badge-modal">
          <div className="badge-modal__header">
            <Text variant="header-2" as="h2">
              Бейдж для README
            </Text>
            <Text variant="body-1" color="secondary">
              Вставьте этот бейдж в README.md репозитория на SourceCraft или GitHub, чтобы показывать актуальный статус
              здоровья проекта.
            </Text>
          </div>

          <div className="badge-modal__preview">
            <Text variant="body-2" color="secondary">
              Предпросмотр:
            </Text>
            <img src={repoBadgeUrl} alt="Repo Health Badge" />
          </div>

          <div className="badge-modal__section">
            <Text variant="subheader-1">Markdown (рекомендуется для README.md)</Text>
            <div className="badge-modal__input-row">
              <TextInput value={markdownSnippet} readOnly hasClear={false} />
              <Button
                view={copiedKey === "md" ? "action" : "normal"}
                onClick={() => void copyToClipboard(markdownSnippet, "md")}
              >
                <Icon data={copiedKey === "md" ? Check : Copy} size={16} />
                {copiedKey === "md" ? "Скопировано" : "Копировать"}
              </Button>
            </div>
          </div>

          <div className="badge-modal__section">
            <Text variant="subheader-1">HTML</Text>
            <div className="badge-modal__input-row">
              <TextInput value={htmlSnippet} readOnly hasClear={false} />
              <Button
                view={copiedKey === "html" ? "action" : "normal"}
                onClick={() => void copyToClipboard(htmlSnippet, "html")}
              >
                <Icon data={copiedKey === "html" ? Check : Copy} size={16} />
                {copiedKey === "html" ? "Скопировано" : "Копировать"}
              </Button>
            </div>
          </div>

          <div className="badge-modal__section">
            <Text variant="subheader-1">Прямая ссылка на SVG</Text>
            <div className="badge-modal__input-row">
              <TextInput value={repoBadgeUrl} readOnly hasClear={false} />
              <Button
                view={copiedKey === "url" ? "action" : "normal"}
                onClick={() => void copyToClipboard(repoBadgeUrl, "url")}
              >
                <Icon data={copiedKey === "url" ? Check : Copy} size={16} />
                {copiedKey === "url" ? "Скопировано" : "Копировать"}
              </Button>
            </div>
          </div>

          <div className="badge-modal__footer">
            <Button view="flat" size="l" onClick={() => setOpen(false)}>
              Закрыть
            </Button>
          </div>
        </div>
      </Modal>
    </>
  );
}
