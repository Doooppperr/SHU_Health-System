import { defineStore } from "pinia";

import { getMe, login, logout as requestLogout, refresh, register } from "../api/auth";
import { returnFriendSession, switchFriendSession } from "../api/friends";
import { clearAllAiSessionStorage } from "../utils/aiSession";

const STORAGE_KEY = "health-system-auth";
let refreshInFlight = null;
let refreshTokenInFlight = "";

function accountName(user) {
  return user?.real_name || user?.display_name || user?.username || "上一级账号";
}

export const useAuthStore = defineStore("auth", {
  state: () => ({
    accessToken: "",
    refreshToken: "",
    user: null,
    delegation: null,
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
          this.delegation = parsed.delegation || null;
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
          ...(this.delegation ? { delegation: this.delegation } : {}),
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
        this.delegation = null;
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
      this.delegation = null;
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

    async switchToFriend(relation) {
      const relationId = relation?.id || relation?.relation_id;
      if (!relationId) throw new Error("缺少亲友授权关系");
      const accountTrail = [
        ...(this.delegation?.accountTrail || []),
        { id: this.user?.id || null, name: accountName(this.user) },
      ];
      const { data } = await switchFriendSession(relationId);
      const delegatedUser = data.user || data.item?.user || data.delegation?.user;
      const delegatedToken = data.access_token || data.token || data.delegation?.access_token;
      if (!delegatedUser || !delegatedToken) {
        throw new Error("亲友账号切换响应不完整");
      }

      clearAllAiSessionStorage();
      this.accessToken = delegatedToken;
      this.refreshToken = data.refresh_token || data.delegation?.refresh_token || "";
      this.user = delegatedUser;
      this.delegation = {
        relationId,
        ownerUsername:
          relation?.friend_username
          || relation?.username
          || delegatedUser.display_name
          || delegatedUser.username
          || "亲友",
        expiresAt:
          data.expires_at
          || data.session?.expires_at
          || data.delegation?.expires_at
          || data.delegation?.session?.expires_at
          || null,
        session: data.session || data.delegation?.session || null,
        previousAccountName: accountTrail.at(-1)?.name || "上一级账号",
        accountTrail,
      };
      this.persist();
      return data;
    },

    async returnToPreviousAccount() {
      if (!this.delegation) return false;
      const currentTrail = this.delegation.accountTrail || [];
      const { data } = await returnFriendSession();
      const returnedUser = data.user || data.item?.user || data.delegation?.user;
      const returnedToken = data.access_token || data.token || data.delegation?.access_token;
      if (!returnedUser || !returnedToken) {
        throw new Error("返回账号响应不完整");
      }

      clearAllAiSessionStorage();
      this.accessToken = returnedToken;
      this.refreshToken = data.refresh_token || data.delegation?.refresh_token || "";
      this.user = returnedUser;
      const session = data.session || data.delegation?.session || null;
      if (session?.delegated) {
        const accountTrail = currentTrail.slice(0, -1);
        this.delegation = {
          relationId: session.relation_id || null,
          ownerUsername: accountName(returnedUser),
          expiresAt: session.expires_at || null,
          session,
          previousAccountName: accountTrail.at(-1)?.name || "上一级账号",
          accountTrail,
        };
      } else {
        this.delegation = null;
      }
      this.persist();
      return data;
    },

    async secureLogout() {
      try {
        await requestLogout();
      } catch {
        // A network failure must never keep credentials in the browser.
      } finally {
        this.logout();
      }
      return true;
    },

    logout() {
      clearAllAiSessionStorage();
      this.accessToken = "";
      this.refreshToken = "";
      this.user = null;
      this.delegation = null;
      this.persist();
    },
  },
});
