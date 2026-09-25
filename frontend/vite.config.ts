import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// WHY: Vite builds ONLY the M10.4 shell — backend never serves these files (JSON-only API
// under financial_analyst_app/app/), keeping frontend/backend fully decoupled (§0.1 lock).
export default defineConfig({
  plugins: [react()],
});
