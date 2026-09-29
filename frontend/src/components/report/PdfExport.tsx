import { FileArrowDown } from "@gravity-ui/icons";
import { Button, Icon, Text } from "@gravity-ui/uikit";
import { useState } from "react";

import { usesDemo } from "../../api/dataSource";
import type { RepositoryReport } from "../../api/report";
import { downloadBlob } from "../../lib/download";
import { reportFileBase } from "../../lib/reportMarkdown";
import { renderReportPdf } from "../../lib/reportPdfRender";

/*
 * «Скачать PDF» сразу сохраняет файл repo-health-org-repo-дата.pdf — без окна печати.
 * PDF собирается в браузере из того же отчёта, что на странице: светлый, с кликабельными
 * ссылками на факты SourceCraft и текстом, который можно выделить и найти поиском.
 */
export function PdfExport({ report }: { report: RepositoryReport }) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function save(): Promise<void> {
    setBusy(true);
    setFailed(false);
    try {
      const blob = await renderReportPdf(report, {
        demo: usesDemo(report.analysis.id),
        siteUrl: window.location.origin,
      });
      downloadBlob(`${reportFileBase(report)}.pdf`, blob);
    } catch (error) {
      console.error("Не удалось собрать PDF", error);
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button view="outlined" loading={busy} onClick={() => void save()}>
        <Icon data={FileArrowDown} size={16} />
        Скачать PDF
      </Button>
      {failed && (
        <Text variant="body-1" color="danger" className="report__export-error" role="alert">
          Не удалось собрать PDF. Обновите страницу и попробуйте ещё раз — или скачайте .md.
        </Text>
      )}
    </>
  );
}
