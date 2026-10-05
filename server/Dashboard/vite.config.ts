import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => ({
  plugins: [react(), {
    name: "frontend-demo-csp",
    // Direct static-file deployments may not apply vercel.json headers.
    // Keep the public DEMO offline without limiting the ordinary LIVE build.
    transformIndexHtml: () => mode === "frontend" ? [{
      tag: "meta",
      attrs: {
        "http-equiv": "Content-Security-Policy",
        content: "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'",
      },
      injectTo: "head",
    }] : [],
  }],
  server: {
    host: "127.0.0.1",
    port: 4173,
    proxy: {
      "/dashboard-api": {
        target: "http://127.0.0.1:8002",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/dashboard-api/, ""),
      },
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    proxy: {
      "/dashboard-api": {
        target: "http://127.0.0.1:8002",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/dashboard-api/, ""),
      },
    },
  },
}));
