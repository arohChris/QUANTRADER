import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

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
