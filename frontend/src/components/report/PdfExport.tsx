import { FileArrowDown } from "@gravity-ui/icons";
import { Button, Icon } from "@gravity-ui/uikit";

import type { RepositoryReport } from "../../api/report";
import { reportFileBase } from "../../lib/reportMarkdown";

/*
 * PDF собирает сам браузер из печатной версии отчёта — она светлая в любой теме. В окне
 * сохранения выбирается «Сохранить как PDF». Имя файла браузер берёт из заголовка документа,
 * поэтому на время сохранения заголовок — то же имя, что у Markdown: repo-health-org-repo-дата.pdf.
 */
export function PdfExport({ report }: { report: RepositoryReport }) {
  function save(): void {
    const title = document.title;
    const restore = (): void => {
      document.title = title;
      window.removeEventListener("afterprint", restore);
    };
    window.addEventListener("afterprint", restore);
    document.title = reportFileBase(report);
    window.print();
  }

  return (
    <Button view="outlined" onClick={save}>
      <Icon data={FileArrowDown} size={16} />
      Скачать PDF
    </Button>
  );
}
