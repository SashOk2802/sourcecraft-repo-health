/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Откуда брать данные: api — только API (по умолчанию); demo — только демо;
   * auto — API, а где раздела нет или он не настроен — демо с пометкой.
   */
  readonly VITE_DATA_SOURCE?: string;
  /** "true" — явно включить демо-данные для офлайн-показа; без флага используется backend API. */
  readonly VITE_USE_MOCKS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
