import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// npm run dev: 画面は http://localhost:5173、/api は audio2chordpro serve（:8000）へ中継する
// npm run build: audio2chordpro/server/static に書き出す（audio2chordpro serve が / で配る）
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../audio2chordpro/server/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
