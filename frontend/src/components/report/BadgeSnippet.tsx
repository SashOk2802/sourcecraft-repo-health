import { Check, Copy, Shield } from "@gravity-ui/icons";
import { Button, Icon, Modal, Text, TextInput } from "@gravity-ui/uikit";
import { useState } from "react";

import type { RepositoryReport } from "../../api/report";
import "./BadgeSnippet.css";

interface BadgeSnippetProps {
  report: RepositoryReport;
}

export function BadgeSnippet({ report }: BadgeSnippetProps) {
  const [open, setOpen] = useState(false);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const { repository, analysis } = report;
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const repoBadgeUrl = `${origin}/api/v1/repositories/${encodeURIComponent(
    repository.organizationSlug,
  )}/${encodeURIComponent(repository.repositorySlug)}/badge.svg`;
  const analysisPageUrl = `${origin}/analyses/${encodeURIComponent(analysis.id)}`;

  const markdownSnippet = `[![Repo Health](${repoBadgeUrl})](${analysisPageUrl})`;
  const htmlSnippet = `<a href="${analysisPageUrl}"><img src="${repoBadgeUrl}" alt="Repo Health"></a>`;

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
