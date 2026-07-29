import { describe, expect, it } from "vitest";

import {
  agentToolProgressLabel,
  replacePendingAgentAction,
} from "./agentPresentation";

describe("Agent user-facing progress", () => {
  it("uses business language instead of internal tool names", () => {
    expect(agentToolProgressLabel("compare_packages")).toBe("正在核对体检套餐");
    expect(agentToolProgressLabel("create_booking_draft")).toBe("正在准备预约确认信息");
  });

  it("does not expose unknown internal identifiers", () => {
    expect(agentToolProgressLabel("internal_tool_name")).toBe("正在核对相关信息");
  });

  it("replaces an older draft of the same action type", () => {
    const actions = [
      { action_id: "booking-old", action_type: "booking" },
      { action_id: "support", action_type: "support_handoff" },
    ];
    expect(replacePendingAgentAction(actions, {
      action_id: "booking-new",
      action_type: "booking",
    })).toEqual([
      { action_id: "support", action_type: "support_handoff" },
      { action_id: "booking-new", action_type: "booking" },
    ]);
  });
});
