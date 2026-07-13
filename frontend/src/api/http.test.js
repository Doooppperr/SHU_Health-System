import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { refresh } from "./auth";
import http from "./http";
import { useAuthStore } from "../stores/auth";

const originalAdapter = http.defaults.adapter;

beforeEach(() => {
  setActivePinia(createPinia());
  localStorage.clear();
});

afterEach(() => {
  http.defaults.adapter = originalAdapter;
  vi.restoreAllMocks();
});

describe("HTTP authorization headers", () => {
  it("preserves the explicit refresh token instead of replacing it with access token", async () => {
    const authStore = useAuthStore();
    authStore.accessToken = "expired-access-token";
    authStore.refreshToken = "valid-refresh-token";
    const adapter = vi.fn(async (config) => ({
      data: { access_token: "new-access-token" },
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    }));
    http.defaults.adapter = adapter;

    await refresh("valid-refresh-token");

    const requestConfig = adapter.mock.calls[0][0];
    expect(requestConfig.url).toBe("/auth/refresh");
    expect(requestConfig.headers.get("Authorization")).toBe(
      "Bearer valid-refresh-token"
    );
  });
});
