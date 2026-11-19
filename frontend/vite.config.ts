import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev proxy: the dashboard talks to the FastAPI serving layer on 8010.
// In MOCK mode (VITE_MOCK=1) the fetch layer never hits the network, so the
// proxy is irrelevant — the UI renders entirely from the built-in fixture.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8010",
        changeOrigin: true,
      },
    },
  },
});
