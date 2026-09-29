/*
 * Браузерная сборка pdfmake 0.3 без собственных типов: описываем только то, чем пользуемся
 * (src/lib/reportPdfRender.ts). Документ собирается в src/lib/reportPdf.ts.
 */
declare module "pdfmake/build/pdfmake" {
  interface CreatedPdf {
    getBlob(): Promise<Blob>;
  }

  interface PdfMake {
    addVirtualFileSystem(vfs: Record<string, string>): void;
    createPdf(documentDefinition: object): CreatedPdf;
  }

  const pdfMake: PdfMake;
  export default pdfMake;
}

declare module "pdfmake/build/vfs_fonts" {
  /** Шрифт Roboto (с кириллицей) в base64 — встраивается в PDF, из сети ничего не грузится. */
  const vfs: Record<string, string>;
  export default vfs;
}
