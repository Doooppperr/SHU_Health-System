import { describe, expect, it } from "vitest";
import { appPath } from "./appBase";

describe("appPath", () => {
  it("keeps root deployments unchanged", () => {
    expect(appPath("/api", "/")).toBe("/api");
  });

  it("prefixes IP subpath deployments", () => {
    expect(appPath("/api/agent", "/healthdoc/")).toBe("/healthdoc/api/agent");
  });
});
