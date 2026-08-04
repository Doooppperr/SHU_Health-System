import { flushPromises, mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import WorkspaceLayout from "./WorkspaceLayout.vue";
import { useAuthStore } from "../stores/auth";

const mocks = vi.hoisted(() => ({
  route: { meta: { title: "健康总览" }, fullPath: "/dashboard" },
  router: { replace: vi.fn() },
  fetchFriends: vi.fn(),
  fetchUnreadCommentReplyCount: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRoute: () => mocks.route,
  useRouter: () => mocks.router,
}));
vi.mock("../api/friends", () => ({ fetchFriends: mocks.fetchFriends }));
vi.mock("../api/comments", () => ({
  fetchUnreadCommentReplyCount: mocks.fetchUnreadCommentReplyCount,
}));

const originalMatchMedia = window.matchMedia;
let wrapper;

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  });
  mocks.fetchUnreadCommentReplyCount.mockResolvedValue({ data: { count: 0 } });
  mocks.fetchFriends.mockResolvedValue({
    data: {
      items: [{
        id: 17,
        relationship_status: "active",
        can_switch: true,
        counterparty: { id: 2, display_name: "亲友甲" },
      }],
    },
  });
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: originalMatchMedia,
  });
});

describe("工作台关联账号入口", () => {
  it("从左下角账号菜单反复切换，并与退出登录放在一起", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore(pinia);
    auth.user = { id: 1, username: "test1", real_name: "本人", role: "user" };
    auth.switchToFriend = vi.fn(async () => {
      auth.user = { id: 2, username: "test2", real_name: "亲友甲", role: "user" };
      auth.delegation = {
        relationId: 17,
        ownerUsername: "亲友甲",
        session: { chain: [1, 2], depth: 1 },
      };
    });
    auth.secureLogout = vi.fn(async () => {
      auth.logout();
      return true;
    });

    wrapper = mount(WorkspaceLayout, {
      attachTo: document.body,
      global: {
        plugins: [pinia, ElementPlus],
        stubs: {
          RouterLink: { template: "<a><slot /></a>" },
          RouterView: { template: "<div />" },
          NotificationCenter: true,
          AiAssistantLauncher: true,
          AppearanceQuickControls: true,
          BasicProfileGate: true,
          teleport: true,
        },
      },
    });
    await flushPromises();

    expect(mocks.fetchFriends).toHaveBeenCalled();
    const footer = wrapper.get(".workspace-sidebar-footer");
    expect(footer.text()).toContain("切换账号");
    expect(footer.text()).toContain("退出登录");
    expect(wrapper.get(".workspace-top-actions").text()).not.toContain("切换账号");
    expect(wrapper.findComponent({ name: "BasicProfileGate" }).exists()).toBe(true);
    await wrapper.vm.switchRelatedAccount(17);
    await flushPromises();
    expect(auth.switchToFriend).toHaveBeenCalledWith(expect.objectContaining({ id: 17 }));
    expect(mocks.router.replace).toHaveBeenCalledWith({ name: "timeline" });
    expect(wrapper.findComponent({ name: "BasicProfileGate" }).exists()).toBe(true);
    expect(wrapper.text()).not.toContain("返回 本人");

    await wrapper.vm.logout();
    expect(auth.secureLogout).toHaveBeenCalled();
    expect(mocks.router.replace).toHaveBeenLastCalledWith({ name: "login" });
  });
});
