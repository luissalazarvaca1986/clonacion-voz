import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// El backend (FastAPI) sirve la API en /api. En desarrollo Vite la reenvía:
// BACKEND apunta al contenedor clonar-voz o a un `python app.py` local.
const BACKEND = process.env.BACKEND || "http://127.0.0.1:8093";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": { target: BACKEND, changeOrigin: true },
    },
  },
});
