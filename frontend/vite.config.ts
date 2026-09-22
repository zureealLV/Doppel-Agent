import { fileURLToPath, URL } from "node:url";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
  base: "/runtime/",
  plugins: [vue()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: {
    outDir: "../src/doppel_agent/web/frontend_dist",
    emptyOutDir: true,
    // Do not ship source maps in the release bundle. Vite embeds sourcesContent,
    // whose checkout line endings made the committed artifact differ across
    // Windows machines even though the executable JavaScript was identical.
    sourcemap: false,
  },
});
