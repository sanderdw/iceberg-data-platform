import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The gateway serves the build from conversationalbi/public; `npm run dev` proxies the API to it.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../conversationalbi/public", emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 4000 },
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:3007", "/auth": "http://localhost:3007" },
  },
  test: { environment: "jsdom", globals: false },
});
