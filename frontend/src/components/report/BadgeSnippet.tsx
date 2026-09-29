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
  const publicApiUrl = `${origin}/api/v1/public/repositories/${encodeURIComponent(
    repository.organizationSlug,
  )}/${encodeURIComponent(repository.repositorySlug)}/health`;

  const markdownSnippet = `[![Repo Health](${repoBadgeUrl})](${analysisPageUrl})`;
  const htmlSnippet = `<a href="${analysisPageUrl}"><img src="${repoBadgeUrl}" alt="Repo Health"></a>`;

  return {
    repoBadgeUrl,
    analysisPageUrl,
    publicApiUrl,
    curlSnippet: `curl "${publicApiUrl}"`,
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

  const { repoBadgeUrl, publicApiUrl, curlSnippet, markdownSnippet, htmlSnippet } = buildBadgeSnippets(report);

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
        Бейдж и API
      </Button>

      <Modal open={open} onClose={() => setOpen(false)}>
        <div className="badge-modal">
          <div className="badge-modal__header">
            <Text variant="header-2" as="h2">
              Бейдж и публичный API
            </Text>
            <Text variant="body-1" color="secondary">
              Вставьте этот бейдж в README.md репозитория на SourceCraft или GitHub, чтобы показывать актуальный статус
              здоровья проекта. API можно использовать для своего сайта, дашборда или бота.
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

          <div className="badge-modal__section">
            <Text variant="subheader-1">Публичный API</Text>
            <Text variant="body-2" color="secondary">
              JSON без авторизации: только Score, категории и статус свежести для репозитория, который SourceCraft
              подтверждает как публичный.
            </Text>
            <div className="badge-modal__input-row">
              <TextInput value={publicApiUrl} readOnly hasClear={false} />
              <Button
                view={copiedKey === "api-url" ? "action" : "normal"}
                onClick={() => void copyToClipboard(publicApiUrl, "api-url")}
              >
                <Icon data={copiedKey === "api-url" ? Check : Copy} size={16} />
                {copiedKey === "api-url" ? "Скопировано" : "Копировать"}
              </Button>
            </div>
            <div className="badge-modal__input-row">
              <TextInput value={curlSnippet} readOnly hasClear={false} />
              <Button
                view={copiedKey === "api-curl" ? "action" : "normal"}
                onClick={() => void copyToClipboard(curlSnippet, "api-curl")}
              >
                <Icon data={copiedKey === "api-curl" ? Check : Copy} size={16} />
                {copiedKey === "api-curl" ? "Скопировано" : "Копировать"}
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
