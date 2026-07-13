import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  refresh: vi.fn(),
}));

vi.mock("../api/auth", () => ({
  getMe: vi.fn(),
  login: vi.fn(),
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
  api.refresh.mockReset();
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
