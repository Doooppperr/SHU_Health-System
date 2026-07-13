import axios from "axios";
import { useAuthStore } from "../stores/auth";

const http = axios.create({
  baseURL: "/api",
  timeout: 10000,
});

http.interceptors.request.use((config) => {
  const authStore = useAuthStore();
  const hasExplicitAuthorization =
    typeof config.headers?.has === "function"
      ? config.headers.has("Authorization")
      : Boolean(config.headers?.Authorization || config.headers?.authorization);
  if (authStore.accessToken && !hasExplicitAuthorization) {
    config.headers.Authorization = `Bearer ${authStore.accessToken}`;
  }
  return config;
});

http.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    const authStore = useAuthStore();

    if (
      error.response?.status === 401 &&
      !originalRequest._retry &&
      !originalRequest.url.includes("/auth/login") &&
      !originalRequest.url.includes("/auth/register") &&
      !originalRequest.url.includes("/auth/refresh") &&
      authStore.refreshToken
    ) {
      originalRequest._retry = true;
      const refreshed = await authStore.tryRefresh();
      if (refreshed) {
        originalRequest.headers.Authorization = `Bearer ${authStore.accessToken}`;
        return http(originalRequest);
      }
    }

    return Promise.reject(error);
  }
);

export default http;
