import { describe, expect, it } from "vitest";

import {
  SELF_OWNER_VALUE,
  buildHealthOwnerOptions,
  ownerRequestParams,
  withOwnerRequestParams,
} from "./healthOwners";

describe("health owner options", () => {
  it("uses authorized outgoing relations as readable health owners", () => {
    const result = buildHealthOwnerOptions(
      {
        outgoing: [
          {
            auth_status: true,
            relation_name: "家人",
            friend_user: { id: 2, display_name: "张丽" },
          },
          {
            auth_status: false,
            relation_name: "同事",
            friend_user: { id: 3, display_name: "王强" },
          },
        ],
        incoming: [
          {
            auth_status: true,
            user: { id: 4, display_name: "查看者" },
          },
        ],
      },
      { username: "test1" }
    );

    expect(result).toEqual([
      { value: SELF_OWNER_VALUE, ownerId: null, label: "本人" },
      { value: "2", ownerId: 2, label: "张丽（家人）" },
    ]);
  });

  it("builds an owner query only for a selected friend", () => {
    expect(ownerRequestParams(SELF_OWNER_VALUE)).toEqual({});
    expect(ownerRequestParams("12")).toEqual({ owner_id: 12 });
    expect(withOwnerRequestParams({ owner_id: "self", page: 2 }, "12")).toEqual({ page: 2, owner_id: 12 });
    expect(withOwnerRequestParams({ owner_id: "12", page: 2 }, SELF_OWNER_VALUE)).toEqual({ page: 2 });
  });
});
