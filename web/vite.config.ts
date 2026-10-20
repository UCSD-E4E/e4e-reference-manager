import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      manifest: {
        name: "e4e Reference Manager",
        short_name: "e4e Refs",
        description: "Collaborative reference manager",
        theme_color: "#1f2937",
        background_color: "#ffffff",
        display: "standalone",
        start_url: "/",
        icons: [
          { src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any maskable" },
        ],
        // Mobile capture: share a paper URL/DOI into the app (wired up in Phase 2).
        share_target: {
          action: "/share-target",
          method: "GET",
          params: { title: "title", text: "text", url: "url" },
        },
      },
      workbox: {
        // Phase 0: cache the app shell only. Read-only metadata caching is Phase 4.
        // Every other path is an SPA route (/libraries/…, /items/…) and must get the
        // shell offline too. In production the API lives only under /api; in dev it is
        // on another origin (VITE_API_URL), which the service worker never intercepts.
        navigateFallbackDenylist: [/^\/api\//],
      },
    }),
  ],
  server: { port: 5173 },
});
