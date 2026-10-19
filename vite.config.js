import { defineConfig } from "vite";

export default defineConfig({
  root: "web",
  base: "./",
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8766",
        changeOrigin: true,
      },
    },
  },
  build: { outDir: "../dist", emptyOutDir: true },
});
