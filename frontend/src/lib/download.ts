/** Сохраняет файл: одинаково для отчёта от backend и собранного в браузере. */
export function downloadBlob(fileName: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.hidden = true;
  document.body.append(link);
  link.click();
  link.remove();
  // Сразу отзывать ссылку нельзя: часть браузеров начинает скачивание асинхронно.
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** Сохраняет текст файлом: одинаково для отчёта от backend и собранного в браузере демо. */
export function downloadText(fileName: string, text: string, type = "text/markdown;charset=utf-8"): void {
  downloadBlob(fileName, new Blob([text], { type }));
}
