import { describe, expect, it } from "vitest";
import { BUILD_INFO, buildLabel } from "./buildInfo";

describe("frontend build information", () => {
  it("exposes a compact release identifier for support screenshots", () => {
    expect(BUILD_INFO.shortCommit.length).toBeGreaterThan(0);
    expect(BUILD_INFO.shortCommit.length).toBeLessThanOrEqual(8);
    expect(buildLabel()).toBe(`版本 ${BUILD_INFO.shortCommit}`);
  });
});
