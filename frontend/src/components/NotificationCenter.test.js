import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import NotificationCenter from "./NotificationCenter.vue";

const mocks = vi.hoisted(() => ({
  router: { push: vi.fn().mockResolvedValue() },
  fetchNotifications: vi.fn(),
  fetchNotificationUnreadCount: vi.fn(),
  markAllNotificationsRead: vi.fn(),
  markNotificationRead: vi.fn(),
}));

vi.mock("vue-router", () => ({ useRouter: () => mocks.router }));
vi.mock("../api/notifications", () => ({
  fetchNotifications: mocks.fetchNotifications,
  fetchNotificationUnreadCount: mocks.fetchNotificationUnreadCount,
  markAllNotificationsRead: mocks.markAllNotificationsRead,
  markNotificationRead: mocks.markNotificationRead,
}));

const wrappers = [];
const notificationPayload = {
  items: [
    {
      id: 1,
      title: "体检报告已交付",
      body: "报告已经可以查看",
      created_at: "2026-07-26T10:00:00Z",
      is_read: false,
      action_url: "/health-data/1",
    },
  ],
  unread_count: 1,
  pagination: { page: 1, page_size: 15, total: 1, pages: 1 },
};

function mountCenter() {
  const wrapper = mount(NotificationCenter, {
    global: {
      stubs: {
        Teleport: true,
        ElBadge: { template: "<div><slot /></div>" },
        ElButton: {
          emits: ["click"],
          template: '<button v-bind="$attrs" @click="$emit(\'click\')"><slot /></button>',
        },
        ElEmpty: { template: "<div>暂无站内通知</div>" },
        ElPagination: { template: "<nav />" },
      },
    },
  });
  wrappers.push(wrapper);
  return wrapper;
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.fetchNotificationUnreadCount.mockResolvedValue({ data: { unread_count: 1 } });
  mocks.fetchNotifications.mockResolvedValue({ data: notificationPayload });
  mocks.markAllNotificationsRead.mockResolvedValue({ data: {} });
  mocks.markNotificationRead.mockResolvedValue({ data: {} });
});

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
});

describe("站内通知中心", () => {
  it("使用明确的通知入口并展示服务端消息", async () => {
    const wrapper = mountCenter();
    await flushPromises();
    expect(wrapper.text()).toContain("通知");

    await wrapper.get('[aria-label="打开站内通知"]').trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("站内通知");
    expect(wrapper.text()).toContain("体检报告已交付");
    expect(mocks.fetchNotifications).toHaveBeenCalledWith({ page: 1, page_size: 15 });

    expect(wrapper.get(".notification-panel").attributes("role")).toBe("dialog");
    expect(wrapper.get(".notification-list").attributes("aria-label")).toBe("通知列表");
    expect(wrapper.get(".notification-list").attributes("tabindex")).toBe("0");
    await wrapper.get('[aria-label="关闭站内通知"]').trigger("click");
    expect(wrapper.find(".notification-panel").exists()).toBe(false);
  });

  it("加载失败时显示原因并允许重新加载", async () => {
    mocks.fetchNotifications
      .mockRejectedValueOnce({ response: { data: { message: "服务暂时不可用" } } })
      .mockResolvedValueOnce({ data: notificationPayload });
    const wrapper = mountCenter();

    await wrapper.get('[aria-label="打开站内通知"]').trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("通知暂时没有加载成功");
    expect(wrapper.text()).toContain("服务暂时不可用");

    const retry = wrapper.get(".notification-error button");
    await retry.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("体检报告已交付");
  });
});
