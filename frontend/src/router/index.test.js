import { describe, expect, it } from "vitest";

import router from "./index";

describe("three-role route isolation", () => {
  it.each([
    ["/dashboard", "user", "dashboard"],
    ["/org/dashboard", "institution_admin", "org-dashboard"],
    ["/admin/dashboard", "admin", "admin-dashboard"],
    ["/appointments", "user", "appointments"],
    ["/org/package-reviews", "institution_admin", "org-package-reviews"],
    ["/admin/package-reviews", "admin", "admin-package-reviews"],
    ["/admin/agent-ops", "admin", "admin-agent-ops"],
  ])("resolves %s only for %s", (path, role, name) => {
    const route = router.resolve(path);
    expect(route.name).toBe(name);
    expect(route.meta.requiresAuth).toBe(true);
    expect(route.meta.roles).toEqual([role]);
  });

  it("keeps the public landing page outside authenticated workspaces", () => {
    const route = router.resolve("/");
    expect(route.name).toBe("public-home");
    expect(route.meta.requiresAuth).not.toBe(true);
  });

  it("routes unknown URLs to the not-found page", () => {
    expect(router.resolve("/missing/page").name).toBe("not-found");
  });

  it("keeps the old measurement URL as a dashboard quick-action redirect", () => {
    const route = router.resolve("/measurements");
    const redirect = route.matched.at(-1).redirect;
    expect(redirect).toEqual({ name: "dashboard", query: { quick: "measurement" } });
  });
});
