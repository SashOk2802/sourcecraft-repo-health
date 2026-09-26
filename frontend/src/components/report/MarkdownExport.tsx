import { FileArrowDown } from "@gravity-ui/icons";
import { Button, Icon, Text } from "@gravity-ui/uikit";
import { useState } from "react";

import { describeError } from "../../api/http";
import { fetchReportMarkdown, type RepositoryReport } from "../../api/report";
import { downloadText } from "../../lib/download";
import { markdownFileName } from "../../lib/reportMarkdown";

/*
 * «Скачать .md» сохраняет файл, а не открывает текст во вкладке: отчёт забирается
 * запросом и отдаётся браузеру как файл с понятным именем repo-health-org-repo-дата.md.
 * Ошибка backend не превращается в скачанный файл с текстом ошибки.
 */
export function MarkdownExport({ report }: { report: RepositoryReport }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  async function save(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      downloadText(markdownFileName(report), await fetchReportMarkdown(report));
    } catch (reason) {
      setError(reason instanceof Error ? reason : new Error(String(reason)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button view="outlined" loading={busy} onClick={() => void save()}>
        <Icon data={FileArrowDown} size={16} />
        Скачать .md
      </Button>
      {error && (
        <Text variant="body-1" color="danger" className="report__export-error" role="alert">
          Не удалось скачать отчёт. {describeError(error)}
        </Text>
      )}
    </>
  );
}
