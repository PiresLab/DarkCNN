import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// build vai direto para o pacote Python (servido pelo FastAPI); em dev o /api é repassado ao backend
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../static", emptyOutDir: true },
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8765" } },
});
