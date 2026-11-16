/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// No dev proxy is configured on purpose: the SPA is served behind the TLS
// reverse proxy, which owns /api routing. Do not add an insecure proxy here.
export default defineConfig({
  base: "/",
  plugins: [react()],
  build: {
    outDir: "dist",
    sourcemap: false,
    // CSP is `default-src 'self'`: keep everything in external same-origin files.
    modulePreload: { polyfill: false },
    assetsInlineLimit: 0,
  },
  test: {
    environment: "jsdom",
    globals: false,
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
