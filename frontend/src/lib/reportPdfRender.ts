import type { RepositoryReport } from "../api/report";
import { buildReportPdf, type ReportPdfOptions } from "./reportPdf";

/**
 * Собирает PDF-файл отчёта. pdfmake и шрифт (~2 МБ) скачиваются только здесь, при первом
 * нажатии «Скачать PDF»: остальному сайту они не нужны.
 */
export async function renderReportPdf(report: RepositoryReport, options: ReportPdfOptions = {}): Promise<Blob> {
  const [{ default: pdfMake }, { default: vfs }] = await Promise.all([
    import("pdfmake/build/pdfmake"),
    import("pdfmake/build/vfs_fonts"),
  ]);
  pdfMake.addVirtualFileSystem(vfs);
  return pdfMake.createPdf(buildReportPdf(report, options)).getBlob();
}
