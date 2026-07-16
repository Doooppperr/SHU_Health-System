import { defineStore } from "pinia";

import { getMe, login, refresh, register } from "../api/auth";
import { clearAllAiSessionStorage } from "../utils/aiSession";

const STORAGE_KEY = "health-system-auth";
let refreshInFlight = null;
let refreshTokenInFlight = "";

export const useAuthStore = defineStore("auth", {
  state: () => ({
    accessToken: "",
    refreshToken: "",
    user: null,
    hydrated: false,
  }),
  actions: {
    hydrate() {
      if (this.hydrated) {
        return;
      }

      let raw = sessionStorage.getItem(STORAGE_KEY);
      if (!raw) {
        // One-time migration from the former cross-tab storage. The legacy
        // value cannot represent different accounts, so claim it for the
        // first hydrated tab and remove the shared copy immediately.
        raw = localStorage.getItem(STORAGE_KEY);
        if (raw) {
          sessionStorage.setItem(STORAGE_KEY, raw);
          localStorage.removeItem(STORAGE_KEY);
        }
      }
      if (raw) {
        try {
          const parsed = JSON.parse(raw);
          this.accessToken = parsed.accessToken || "";
          this.refreshToken = parsed.refreshToken || "";
          this.user = parsed.user || null;
        } catch {
          sessionStorage.removeItem(STORAGE_KEY);
          localStorage.removeItem(STORAGE_KEY);
        }
      }

      this.hydrated = true;
    },

    persist() {
      localStorage.removeItem(STORAGE_KEY);
      if (!this.accessToken && !this.refreshToken && !this.user) {
        sessionStorage.removeItem(STORAGE_KEY);
        return;
      }
      sessionStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          accessToken: this.accessToken,
          refreshToken: this.refreshToken,
          user: this.user,
        })
      );
    },

    async registerUser(payload) {
      const { data } = await register(payload);
      if (data.access_token && data.refresh_token) {
        clearAllAiSessionStorage();
        this.accessToken = data.access_token;
        this.refreshToken = data.refresh_token;
        this.user = data.user;
        this.persist();
      }
      return data;
    },

    async loginUser(payload) {
      const { data } = await login(payload);
      clearAllAiSessionStorage();
      this.accessToken = data.access_token;
      this.refreshToken = data.refresh_token;
      this.user = data.user;
      this.persist();
      return data;
    },

    async fetchMe() {
      const { data } = await getMe();
      this.user = data.user;
      this.persist();
      return data.user;
    },

    async tryRefresh() {
      if (!this.refreshToken) {
        return false;
      }

      const requestedToken = this.refreshToken;
      if (refreshInFlight && refreshTokenInFlight === requestedToken) {
        return refreshInFlight;
      }

      const refreshTask = (async () => {
        try {
          const { data } = await refresh(requestedToken);
          if (this.refreshToken !== requestedToken) {
            return false;
          }
          this.accessToken = data.access_token;
          this.persist();
          return true;
        } catch {
          if (this.refreshToken === requestedToken) {
            this.logout();
          }
          return false;
        }
      })();

      refreshInFlight = refreshTask;
      refreshTokenInFlight = requestedToken;
      try {
        return await refreshTask;
      } finally {
        if (refreshInFlight === refreshTask) {
          refreshInFlight = null;
          refreshTokenInFlight = "";
        }
      }
    },

    logout() {
      clearAllAiSessionStorage();
      this.accessToken = "";
      this.refreshToken = "";
      this.user = null;
      this.persist();
    },
  },
});
