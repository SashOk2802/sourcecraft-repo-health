/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Откуда брать данные: auto — API, а где раздела нет — демо; api — только API;
   * demo — только демо. По умолчанию выбирает vite.config.ts.
   */
  readonly VITE_DATA_SOURCE?: string;
  /** Устаревшее: "true" — демо, "false" — только API. */
  readonly VITE_USE_MOCKS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
