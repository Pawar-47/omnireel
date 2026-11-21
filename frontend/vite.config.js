import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const target = process.env.OMNIREEL_BACKEND || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
