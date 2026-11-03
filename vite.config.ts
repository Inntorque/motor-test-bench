import { defineConfig } from "vite";

export default defineConfig({
  root: "web",
  base: "./",
  server: {
    port: 5174,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:5173",
        changeOrigin: true,
      },
    },
  },
  build: { outDir: "../dist", emptyOutDir: true },
});
