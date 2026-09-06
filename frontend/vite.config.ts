import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Keep browser requests same-origin while the app-owned Python
      // workbench transport runs separately during local development.
      "/api": "http://127.0.0.1:8765",
    },
  },
});
