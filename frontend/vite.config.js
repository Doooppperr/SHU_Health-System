import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const apiProxyTarget = process.env.VITE_API_PROXY_TARGET || "http://127.0.0.1:5050";
const backendProxy = {
  target: apiProxyTarget,
  changeOrigin: true,
};

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/api": backendProxy,
      "/uploads": backendProxy,
    },
  },
  preview: {
    proxy: {
      "/api": backendProxy,
      "/uploads": backendProxy,
    },
  },
  build: {
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) {
            return;
          }
          if (id.includes("echarts")) {
            return "echarts";
          }
          if (id.includes("element-plus")) {
            return "element-plus";
          }
          if (id.includes("vue") || id.includes("pinia") || id.includes("vue-router")) {
            return "vue-core";
          }
          return "vendor";
        },
      },
    },
  },
});
