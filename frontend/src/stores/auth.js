import { defineStore } from "pinia";

import { getMe, login, refresh, register } from "../api/auth";

const STORAGE_KEY = "health-system-auth";

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

      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        try {
          const parsed = JSON.parse(raw);
          this.accessToken = parsed.accessToken || "";
          this.refreshToken = parsed.refreshToken || "";
          this.user = parsed.user || null;
        } catch {
          localStorage.removeItem(STORAGE_KEY);
        }
      }

      this.hydrated = true;
    },

    persist() {
      localStorage.setItem(
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
        this.accessToken = data.access_token;
        this.refreshToken = data.refresh_token;
        this.user = data.user;
        this.persist();
      }
      return data;
    },

    async loginUser(payload) {
      const { data } = await login(payload);
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

      try {
        const { data } = await refresh(this.refreshToken);
        this.accessToken = data.access_token;
        this.persist();
        return true;
      } catch {
        this.logout();
        return false;
      }
    },

    logout() {
      this.accessToken = "";
      this.refreshToken = "";
      this.user = null;
      this.persist();
    },
  },
});
