/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "false" — ходить в настоящий API вместо mock-данных. */
  readonly VITE_USE_MOCKS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
