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

describe("关联账号重复切换", () => {
  it("链路中的账号仍然使用相同的切换操作", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore(pinia);
    auth.user = { id: 4, display_name: "亲友丙", role: "user" };
    auth.delegation = {
      session: { chain: [1, 2, 4], depth: 2 },
    };
    auth.switchToFriend = vi.fn(async () => {
      auth.user = { id: 2, display_name: "亲友乙", role: "user" };
    });

    const wrapper = mount(FriendManageView, {
      global: { plugins: [pinia, ElementPlus] },
    });
    await flushPromises();

    const switchButton = wrapper.findAll("button").find(
      (button) => button.text().includes("切换至此账号")
    );
    expect(switchButton).toBeTruthy();
    await switchButton.trigger("click");
    await flushPromises();

    expect(auth.switchToFriend).toHaveBeenCalledWith(expect.objectContaining({ id: 24 }));
    expect(mocks.router.push).toHaveBeenCalledWith({ name: "timeline" });
    wrapper.unmount();
  });
});
