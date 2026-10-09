import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// WHY declared rather than `npm i -D @types/node`: this file runs in Node, so `process`
// exists at runtime - only its TYPE is absent, and `@types/node` is a whole dependency tree
// pulled in for one value. A local declaration keeps the override configurable without it.
declare const process: { env: Record<string, string | undefined> };

// WHY: Vite builds ONLY the M10.4 shell — backend never serves these files (JSON-only API
// under financial_analyst_app/app/), keeping frontend/backend fully decoupled (§0.1 lock).
//
// WHY manualChunks: the app shipped as one JS chunk that crossed Vite's 500 kB warning
// line and kept climbing each round. Splitting the framework out fixes two things at once —
// the warning goes, and a content change (the generated news snapshot, market data) no
// longer invalidates the framework cache in a returning visitor's browser.
//
// WHY only two extra chunks and not one per package: react, react-dom and react-router-dom
// import each other's dependencies, so giving them a chunk separate from the rest produced
// a CIRCULAR vendor -> framework -> vendor chunk, which Rollup warns about and which can
// bite on load order. react + react-router-dom + the remaining node_modules therefore share
// ONE `vendor` chunk. lightweight-charts and lucide-react are split because they are large
// and genuinely independent.
export default defineConfig({
  plugins: [react()],
  server: {
    // WHY a dev proxy and not an absolute backend URL in the client: the browser would then
    // treat the API as cross-origin, so every request needs CORS preflight AND the app
    // hardcodes an environment-specific host. Proxying keeps the browser on one origin in
    // dev and the build fully environment-agnostic - the built bundle calls a RELATIVE
    // /api/v1/... path and works unchanged behind whatever serves it.
    //
    // WHY configurable: the monitor compose stack runs the API on a different port, and a
    // hardcoded target would make the frontend unusable there.
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (!id.includes("node_modules")) {
            return undefined;
          }
          // The charting library is the single largest dependency and changes rarely.
          if (id.includes("lightweight-charts")) {
            return "charts";
          }
          if (id.includes("lucide-react")) {
            return "icons";
          }
          // react, react-dom and react-router-dom land here too, deliberately: see above.
          return "vendor";
        },
      },
    },
  },
});
