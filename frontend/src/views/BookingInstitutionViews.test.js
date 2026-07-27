import { flushPromises, mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AppointmentBookingView from "./AppointmentBookingView.vue";
import InstitutionListView from "./InstitutionListView.vue";
import { useAuthStore } from "../stores/auth";

const mocks = vi.hoisted(() => ({
  router: { push: vi.fn() },
  fetchOrganizations: vi.fn(),
  fetchFriends: vi.fn(),
  fetchAppointmentAvailability: vi.fn(),
  fetchBookingIntakeDefaults: vi.fn(),
  fetchBookingGroups: vi.fn(),
  fetchWaitlistSubscriptions: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => mocks.router,
}));
vi.mock("../api/institutions", () => ({
  fetchOrganizations: mocks.fetchOrganizations,
}));
vi.mock("../api/friends", () => ({
  fetchFriends: mocks.fetchFriends,
}));
vi.mock("../api/appointments", () => ({
  cancelBookingGroup: vi.fn(),
  cancelWaitlistSubscription: vi.fn(),
  createBookingGroup: vi.fn(),
  createWaitlistSubscription: vi.fn(),
  fetchAppointmentAvailability: mocks.fetchAppointmentAvailability,
  fetchBookingIntakeDefaults: mocks.fetchBookingIntakeDefaults,
  fetchBookingGroups: mocks.fetchBookingGroups,
  fetchWaitlistSubscriptions: mocks.fetchWaitlistSubscriptions,
}));

const wrappers = [];

function mountView(component) {
  const pinia = createPinia();
  setActivePinia(pinia);
  useAuthStore(pinia).user = {
    id: 1,
    username: "test1",
    real_name: "林晓晨",
    role: "user",
  };
  const wrapper = mount(component, {
    attachTo: document.body,
    global: {
      plugins: [pinia, ElementPlus],
      stubs: { teleport: true },
    },
  });
  wrappers.push(wrapper);
  return wrapper;
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.fetchOrganizations.mockResolvedValue({ data: { items: [] } });
  mocks.fetchFriends.mockResolvedValue({ data: { outgoing: [] } });
  mocks.fetchAppointmentAvailability.mockResolvedValue({ data: { items: [] } });
  mocks.fetchBookingIntakeDefaults.mockResolvedValue({
    data: { item: { height_cm: 175, weight_kg: 72 } },
  });
  mocks.fetchBookingGroups.mockResolvedValue({
    data: {
      items: [{
        id: 1,
        appointment_date: "2026-07-26",
        status_codes: ["fulfilled"],
        package: { name: "综合体检" },
        institution: { name: "澄心健康管理中心", branch_name: "徐汇综合院区" },
        participant_names: ["林晓晨"],
        party_size: 1,
        can_cancel: false,
      }],
      pagination: { page: 1, page_size: 10, total: 11, pages: 2 },
    },
  });
  mocks.fetchWaitlistSubscriptions.mockResolvedValue({
    data: {
      items: [],
      active_count: 0,
      pagination: { page: 1, page_size: 15, total: 0, pages: 0 },
    },
  });
});

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  document.body.innerHTML = "";
  vi.useRealTimers();
});

describe("预约记录分页", () => {
  it("固定每页十组并明确显示当前页", async () => {
    const wrapper = mountView(AppointmentBookingView);
    await flushPromises();

    expect(mocks.fetchBookingGroups).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, page_size: 10 }),
    );
    expect(wrapper.get(".booking-pagination__summary").text()).toContain("第 1 / 2 页");
    expect(wrapper.get(".booking-pagination__summary").text()).toContain("每页 10 组");
    expect(wrapper.find(".el-pagination").exists()).toBe(true);
  });
});

describe("体检机构目录搜索", () => {
  it("防抖发送搜索词并只渲染后端匹配结果", async () => {
    vi.useFakeTimers();
    mocks.fetchOrganizations
      .mockResolvedValueOnce({
        data: {
          items: [{
            id: 1,
            name: "澄心健康管理中心",
            description: "综合健康管理机构",
            service_features: [],
            branches: [{
              id: 11,
              branch_name: "徐汇综合院区",
              district: "徐汇区",
              address: "斜土路1609号",
              package_count: 3,
              images: [],
            }],
          }],
        },
      })
      .mockResolvedValueOnce({
        data: {
          items: [{
            id: 2,
            name: "衡康代谢与慢病管理中心",
            description: "代谢专项机构",
            service_features: [],
            branches: [{
              id: 22,
              branch_name: "浦东陆家嘴院区",
              district: "浦东新区",
              address: "浦东南路855号",
              package_count: 2,
              images: [],
            }],
          }],
        },
      })
      .mockResolvedValueOnce({
        data: {
          items: [{
            id: 1,
            name: "澄心健康管理中心",
            description: "综合健康管理机构",
            service_features: [],
            branches: [{
              id: 11,
              branch_name: "徐汇综合院区",
              district: "徐汇区",
              address: "斜土路1609号",
              package_count: 3,
              images: [],
            }],
          }],
        },
      });

    const wrapper = mountView(InstitutionListView);
    await flushPromises();
    expect(wrapper.text()).toContain("徐汇综合院区");

    await wrapper.get('input[aria-label="搜索体检机构"]').setValue("陆家嘴");
    await vi.advanceTimersByTimeAsync(300);
    await flushPromises();

    expect(mocks.fetchOrganizations).toHaveBeenLastCalledWith({ q: "陆家嘴" });
    expect(wrapper.text()).toContain("浦东陆家嘴院区");
    expect(wrapper.text()).not.toContain("徐汇综合院区");
    expect(wrapper.text()).toContain("找到 1 家机构主体、1 家分院");

    await wrapper.get('input[aria-label="搜索体检机构"]').setValue("");
    await vi.advanceTimersByTimeAsync(300);
    await flushPromises();

    expect(mocks.fetchOrganizations).toHaveBeenLastCalledWith({ q: "" });
    expect(wrapper.text()).toContain("徐汇综合院区");
  });
});
