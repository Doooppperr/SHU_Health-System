import { describe, expect, it } from "vitest";

import { formatRecordDisplayId } from "./recordDisplayId";

describe("health record display id", () => {
  it("formats numeric API ids without changing the source value", () => {
    const record = { id: 42 };

    expect(formatRecordDisplayId(record)).toBe("health42");
    expect(formatRecordDisplayId("007")).toBe("health007");
    expect(record.id).toBe(42);
  });

  it("prefers server-provided record and trend display ids", () => {
    expect(formatRecordDisplayId({ id: 42, display_id: "health9001" })).toBe(
      "health9001"
    );
    expect(
      formatRecordDisplayId({ record_id: 42, record_display_id: "HEALTH9002" })
    ).toBe("health9002");
  });

  it("falls back to a numeric id when a server display id is malformed", () => {
    expect(formatRecordDisplayId({ id: 42, display_id: "record-42" })).toBe(
      "health42"
    );
  });

  it("never exposes malformed or missing identifiers", () => {
    expect(formatRecordDisplayId(null)).toBe("-");
    expect(formatRecordDisplayId("abc")).toBe("-");
    expect(formatRecordDisplayId({ display_id: "healthabc" })).toBe("-");
  });
});
