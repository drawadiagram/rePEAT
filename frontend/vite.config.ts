import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // SSE needs buffering off; Vite streams proxied responses as-is.
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    // Explicit imports rather than injected globals: a test file should say
    // where `expect` comes from.
    globals: false,
    include: ["src/**/*.test.{ts,tsx}"],
    // `e2e/` is Playwright's (a real browser), and MolstarView wants a real
    // canvas and WebGL — it is covered there, not here.
    exclude: ["e2e/**", "node_modules/**"],
  },
});
