import { flushPromises, mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FriendManageView from "./FriendManageView.vue";
import { useAuthStore } from "../stores/auth";

const mocks = vi.hoisted(() => ({
  router: { push: vi.fn() },
  fetchFriends: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRouter: () => mocks.router,
}));

vi.mock("../api/friends", () => ({
  addFriend: vi.fn(),
  deleteFriend: vi.fn(),
  fetchFriends: mocks.fetchFriends,
  renameFriend: vi.fn(),
  updateFriendAuthorization: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.fetchFriends.mockResolvedValue({
    data: {
      items: [{
        id: 24,
        relationship_status: "active",
        can_switch: true,
        counterparty: { id: 2, display_name: "亲友乙" },
      }],
    },
  });
});

describe("关联账号逐级返回", () => {
  it("把链路中的上一级账号显示为返回操作而不是再次切换", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore(pinia);
    auth.user = { id: 4, display_name: "亲友丙", role: "user" };
    auth.delegation = {
      previousAccountName: "亲友乙",
      session: { chain: [1, 2, 4], depth: 2 },
    };
    auth.returnToPreviousAccount = vi.fn(async () => {
      auth.user = { id: 2, display_name: "亲友乙", role: "user" };
      auth.delegation = { session: { chain: [1, 2], depth: 1 } };
    });
    auth.switchToFriend = vi.fn();

    const wrapper = mount(FriendManageView, {
      global: { plugins: [pinia, ElementPlus] },
    });
    await flushPromises();

    const returnButton = wrapper.findAll("button").find(
      (button) => button.text().includes("返回 亲友乙")
    );
    expect(returnButton).toBeTruthy();
    await returnButton.trigger("click");
    await flushPromises();

    expect(auth.returnToPreviousAccount).toHaveBeenCalledOnce();
    expect(auth.switchToFriend).not.toHaveBeenCalled();
    expect(mocks.router.push).toHaveBeenCalledWith({ name: "timeline" });
    wrapper.unmount();
  });
});
