import { describe, expect, it } from "vitest";

import { buildRegistrationPayload } from "./registration";

const form = {
  username: "  staff-user  ",
  password: "secret123",
  email: " staff@example.com ",
  phone: " 13800000000 ",
  invite_code: " ABCD-EFGH-IJKL-MNPQ ",
  captcha_id: "captcha-id",
  captcha_answer: " a1b2 ",
};

describe("registration payload", () => {
  it("never sends an invitation code for normal-user registration", () => {
    const payload = buildRegistrationPayload("user", form);
    expect(payload).not.toHaveProperty("invite_code");
    expect(payload.username).toBe("staff-user");
    expect(payload.captcha_answer).toBe("a1b2");
  });

  it("never sends an invitation code even if a removed staff mode is passed", () => {
    expect(buildRegistrationPayload("staff", form)).toMatchObject({
      email: "staff@example.com",
      phone: "13800000000",
    });
    expect(buildRegistrationPayload("staff", form)).not.toHaveProperty("invite_code");
  });

  it("always includes the normalized mandatory email while omitting a blank optional phone", () => {
    const payload = buildRegistrationPayload("user", {
      ...form,
      email: " ",
      phone: "",
    });
    expect(payload.email).toBe("");
    expect(payload).not.toHaveProperty("phone");
  });
});
