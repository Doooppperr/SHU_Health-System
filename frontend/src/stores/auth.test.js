import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  login: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("../api/auth", () => ({
  getMe: vi.fn(),
  login: api.login,
  refresh: api.refresh,
  register: vi.fn(),
}));

vi.mock("../utils/aiSession", () => ({
  clearAllAiSessionStorage: vi.fn(),
}));

import { useAuthStore } from "./auth";

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  window.sessionStorage.clear();
  api.login.mockReset();
  api.refresh.mockReset();
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
