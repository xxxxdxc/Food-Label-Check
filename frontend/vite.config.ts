import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// changeOrigin 保持 false：后端会拒绝 Origin 与 Host 不一致的写请求。
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5182,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
      "/openapi.json": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
