/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_FIXTURES?: string
  readonly VITE_FIXTURE_SPEED?: string
}
interface ImportMeta {
  readonly env: ImportMetaEnv
}
