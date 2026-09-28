/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "true" — явно включить fixtures для офлайн-демо; без флага используется backend API. */
  readonly VITE_USE_MOCKS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
