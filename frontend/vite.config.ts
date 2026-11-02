import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Production build goes straight into the Python package, which serves it.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../terminal/web/dist", emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 700 },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
});
