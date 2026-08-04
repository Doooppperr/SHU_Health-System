import { describe, expect, it } from "vitest";

import {
  HEALTH_ID_REDACTION,
  redactHealthIdentityCodes,
} from "./sensitiveData";

describe("health identity code redaction", () => {
  it("redacts exact issued codes recursively and without mutating the input", () => {
    const original = {
      "HID-8K3M2Q7A": [
        "使用 hid-5r9t4w2c 继续",
        { nested: "HID-7N2P6X8D" },
      ],
    };

    const redacted = redactHealthIdentityCodes(original);
    const serialized = JSON.stringify(redacted);

    expect(serialized).not.toContain("HID-8K3M2Q7A");
    expect(serialized).not.toContain("hid-5r9t4w2c");
    expect(serialized).not.toContain("HID-7N2P6X8D");
    expect(serialized.split(HEALTH_ID_REDACTION)).toHaveLength(4);
    expect(JSON.stringify(original)).toContain("HID-8K3M2Q7A");
  });

  it.each([
    "HID-8K3M2Q7",
    "HID-8K3M2Q1A",
    "HID-8K3M2QIA",
  ])("does not broadly filter non-issued shape %s", (value) => {
    expect(redactHealthIdentityCodes(value)).toBe(value);
  });

  it.each([
    "XHID-8K3M2Q7AY",
    "HID-8K3M2Q7A9",
    "中文HID-8K3M2Q7A中文",
  ])("redacts an issued-code substring even when it is wrapped: %s", (value) => {
    const redacted = redactHealthIdentityCodes(value);
    expect(redacted).not.toContain("HID-8K3M2Q7A");
    expect(redacted).toContain(HEALTH_ID_REDACTION);
  });
});
