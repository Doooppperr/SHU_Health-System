import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  login: vi.fn(),
  logout: vi.fn(),
  refresh: vi.fn(),
}));
const friendsApi = vi.hoisted(() => ({
  returnFriendSession: vi.fn(),
  switchFriendSession: vi.fn(),
}));

vi.mock("../api/auth", () => ({
  getMe: vi.fn(),
  login: api.login,
  logout: api.logout,
  refresh: api.refresh,
  register: vi.fn(),
}));

vi.mock("../utils/aiSession", () => ({
  clearAllAiSessionStorage: vi.fn(),
}));
vi.mock("../api/friends", () => friendsApi);

import { useAuthStore } from "./auth";

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  window.sessionStorage.clear();
  api.login.mockReset();
  api.logout.mockReset();
  api.refresh.mockReset();
  friendsApi.returnFriendSession.mockReset();
  friendsApi.switchFriendSession.mockReset();
});

describe("auth tab isolation", () => {
  it("persists credentials in sessionStorage instead of cross-tab localStorage", async () => {
    api.login.mockResolvedValueOnce({
      data: {
        access_token: "access-one",
        refresh_token: "refresh-one",
        user: { id: 1, username: "account-one" },
      },
    });
    const store = useAuthStore();

    await store.loginUser({ username: "account-one" });

    expect(JSON.parse(sessionStorage.getItem("health-system-auth"))).toEqual({
      accessToken: "access-one",
      refreshToken: "refresh-one",
      user: { id: 1, username: "account-one" },
    });
    expect(localStorage.getItem("health-system-auth")).toBeNull();
  });

  it("hydrates two simulated tabs from their own account snapshots", () => {
    const firstTab = JSON.stringify({
      accessToken: "access-one",
      refreshToken: "refresh-one",
      user: { id: 1, username: "account-one" },
    });
    const secondTab = JSON.stringify({
      accessToken: "access-two",
      refreshToken: "refresh-two",
      user: { id: 2, username: "account-two" },
    });

    sessionStorage.setItem("health-system-auth", firstTab);
    const firstStore = useAuthStore();
    firstStore.hydrate();
    expect(firstStore.user.username).toBe("account-one");

    setActivePinia(createPinia());
    sessionStorage.setItem("health-system-auth", secondTab);
    const secondStore = useAuthStore();
    secondStore.hydrate();
    expect(secondStore.user.username).toBe("account-two");
    expect(firstStore.user.username).toBe("account-one");
  });

  it("migrates the former shared login once and removes the legacy value", () => {
    localStorage.setItem(
      "health-system-auth",
      JSON.stringify({
        accessToken: "legacy-access",
        refreshToken: "legacy-refresh",
        user: { id: 9, username: "legacy-account" },
      })
    );
    const store = useAuthStore();

    store.hydrate();

    expect(store.user.username).toBe("legacy-account");
    expect(sessionStorage.getItem("health-system-auth")).not.toBeNull();
    expect(localStorage.getItem("health-system-auth")).toBeNull();
  });

  it("logging out clears only the current tab session", () => {
    const store = useAuthStore();
    store.accessToken = "access";
    store.refreshToken = "refresh";
    store.user = { id: 1 };
    store.persist();

    store.logout();

    expect(sessionStorage.getItem("health-system-auth")).toBeNull();
    expect(store.user).toBeNull();
  });

  it("returns from a delegated account without requiring another login", async () => {
    friendsApi.switchFriendSession.mockResolvedValue({
      data: {
        access_token: "delegated-access",
        refresh_token: "delegated-refresh",
        user: { id: 12, username: "relative", role: "user" },
        session: { actor: { id: 1 }, subject: { id: 12 }, depth: 1 },
      },
    });
    friendsApi.returnFriendSession.mockResolvedValue({
      data: {
        access_token: "returned-access",
        refresh_token: "returned-refresh",
        user: { id: 1, username: "actor", role: "user" },
        session: null,
      },
    });
    const store = useAuthStore();
    store.accessToken = "actor-access";
    store.refreshToken = "actor-refresh";
    store.user = { id: 1, username: "actor", role: "user" };

    await store.switchToFriend({ id: 7, counterparty: { username: "relative" } });
    expect(store.accessToken).toBe("delegated-access");
    expect(store.delegation).toEqual(expect.objectContaining({
      relationId: 7,
      previousAccountName: "actor",
    }));

    await store.returnToPreviousAccount();
    expect(friendsApi.returnFriendSession).toHaveBeenCalledOnce();
    expect(store.accessToken).toBe("returned-access");
    expect(store.refreshToken).toBe("returned-refresh");
    expect(store.user.username).toBe("actor");
    expect(store.delegation).toBeNull();
    expect(JSON.parse(sessionStorage.getItem("health-system-auth"))).toEqual(
      expect.objectContaining({ accessToken: "returned-access" })
    );
  });

  it("uses delegated tokens and clears the complete login chain on logout", async () => {
    friendsApi.switchFriendSession.mockResolvedValue({
      data: {
        access_token: "delegated-access",
        refresh_token: "delegated-refresh",
        user: { id: 12, username: "relative", role: "user" },
        session: { actor: { id: 1 }, subject: { id: 12 }, depth: 1 },
      },
    });
    api.logout.mockResolvedValue({
      data: { message: "delegation ended", redirect_to: "/login" },
    });
    const store = useAuthStore();
    store.accessToken = "actor-access";
    store.refreshToken = "actor-refresh";
    store.user = { id: 1, username: "actor", role: "user" };

    await store.switchToFriend({ id: 7, counterparty: { username: "relative" } });
    expect(store.accessToken).toBe("delegated-access");
    expect(store.delegation).toEqual(expect.objectContaining({ relationId: 7 }));

    await store.secureLogout();
    expect(api.logout).toHaveBeenCalledOnce();
    expect(store.accessToken).toBe("");
    expect(store.user).toBeNull();
    expect(sessionStorage.getItem("health-system-auth")).toBeNull();
  });
});

describe("auth token refresh", () => {
  it("shares one refresh request between concurrent 401 handlers", async () => {
    let resolveRefresh;
    api.refresh.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRefresh = resolve;
        })
    );
    const store = useAuthStore();
    store.accessToken = "expired";
    store.refreshToken = "refresh-token";

    const first = store.tryRefresh();
    const second = store.tryRefresh();
    resolveRefresh({ data: { access_token: "fresh" } });

    await expect(Promise.all([first, second])).resolves.toEqual([true, true]);
    expect(api.refresh).toHaveBeenCalledOnce();
    expect(store.accessToken).toBe("fresh");
  });

  it("clears the expired session when refresh itself is rejected", async () => {
    api.refresh.mockRejectedValueOnce(new Error("unauthorized"));
    const store = useAuthStore();
    store.accessToken = "expired";
    store.refreshToken = "invalid-refresh";
    store.user = { id: 1 };

    await expect(store.tryRefresh()).resolves.toBe(false);

    expect(api.refresh).toHaveBeenCalledOnce();
    expect(store.accessToken).toBe("");
    expect(store.refreshToken).toBe("");
    expect(store.user).toBeNull();
  });
});
