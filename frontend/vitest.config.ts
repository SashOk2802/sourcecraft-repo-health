import { defineConfig, mergeConfig } from "vitest/config";

import viteConfig from "./vite.config.ts";

export default mergeConfig(viteConfig, defineConfig({
  test: {
    // Let Vite handle UIKit CSS when rendering report components in Node tests.
    server: { deps: { inline: [/@gravity-ui\/uikit/] } },
  },
}));
