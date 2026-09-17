import { defineConfig } from "vite";

export default defineConfig({
  root: "web",
  build: {
    outDir: "../web_dist",
    emptyOutDir: true,
  },
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765",
    },
  },
});
