import { flushPromises, mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AppointmentBookingView from "./AppointmentBookingView.vue";
import InstitutionListView from "./InstitutionListView.vue";
import { useAuthStore } from "../stores/auth";
import { bookingDateBounds } from "../utils/v12";

const mocks = vi.hoisted(() => ({
  route: { query: {} },
  router: { push: vi.fn() },
  fetchOrganizations: vi.fn(),
  fetchFriends: vi.fn(),
  fetchAppointmentAvailability: vi.fn(),
  fetchBookingIntakeDefaults: vi.fn(),
  fetchBookingGroups: vi.fn(),
  fetchMyAppointments: vi.fn(),
  fetchWaitlistSubscriptions: vi.fn(),
  fetchMyComplaints: vi.fn(),
  resolveBookingParticipantToken: vi.fn(),
  createBookingGroup: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRoute: () => mocks.route,
  useRouter: () => mocks.router,
}));
vi.mock("../api/complaints", () => ({
  confirmComplaintResolved: vi.fn(),
  createComplaint: vi.fn(),
  escalateComplaint: vi.fn(),
  fetchMyComplaints: mocks.fetchMyComplaints,
}));
vi.mock("../api/institutions", () => ({
  fetchOrganizations: mocks.fetchOrganizations,
}));
vi.mock("../api/friends", () => ({
  fetchFriends: mocks.fetchFriends,
}));
vi.mock("../api/appointments", () => ({
  cancelAppointment: vi.fn(),
  cancelBookingGroup: vi.fn(),
  cancelWaitlistSubscription: vi.fn(),
  createBookingGroup: mocks.createBookingGroup,
  createWaitlistSubscription: vi.fn(),
  fetchAppointmentAvailability: mocks.fetchAppointmentAvailability,
  fetchBookingIntakeDefaults: mocks.fetchBookingIntakeDefaults,
  fetchBookingGroups: mocks.fetchBookingGroups,
  fetchMyAppointments: mocks.fetchMyAppointments,
  fetchWaitlistSubscriptions: mocks.fetchWaitlistSubscriptions,
  resolveBookingParticipantToken: mocks.resolveBookingParticipantToken,
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
  mocks.route.query = {};
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
      pagination: { page: 1, page_size: 50, total: 1, pages: 1 },
    },
  });
  mocks.fetchMyAppointments.mockResolvedValue({
    data: {
      items: [{
        id: 91,
        appointment_date: "2026-07-26",
        status: "fulfilled",
        package_name: "综合体检",
        institution: { name: "澄心健康管理中心", branch_name: "徐汇综合院区" },
        booked_by_user_id: 2,
      }],
      pagination: { page: 1, page_size: 100, total: 1, pages: 1 },
    },
  });
  mocks.fetchWaitlistSubscriptions.mockResolvedValue({
    data: {
      items: [],
      active_count: 0,
      pagination: { page: 1, page_size: 15, total: 0, pages: 0 },
    },
  });
  mocks.fetchMyComplaints.mockResolvedValue({ data: { items: [] } });
  mocks.resolveBookingParticipantToken.mockResolvedValue({
    data: {
      item: {
        participant_token: "bpt-secret",
        real_name: "虚构受检者",
        gender: "female",
        birth_year: 1992,
        masked_health_id: "HE******01",
        has_recent_height: true,
        has_recent_weight: false,
      },
    },
  });
  mocks.createBookingGroup.mockResolvedValue({
    data: { item: { id: 71, appointment_date: "2026-08-01" } },
  });
});

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  document.body.innerHTML = "";
  vi.useRealTimers();
});

describe("预约记录抽屉", () => {
  it("从页面按钮打开统一记录并保留筛选与分页", async () => {
    const wrapper = mountView(AppointmentBookingView);
    await flushPromises();

    expect(mocks.fetchBookingGroups).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, page_size: 50 }),
    );
    expect(mocks.fetchMyAppointments).toHaveBeenCalledWith({ page: 1, page_size: 100 });
    expect(wrapper.text()).toContain("预约记录");
    expect(wrapper.text()).toContain("排队记录");
    const recordButton = wrapper.findAll(".booking-record-entry").find((button) => button.text().includes("预约记录"));
    await recordButton.trigger("click");
    await flushPromises();
    expect(wrapper.vm.bookingDrawerVisible).toBe(true);
    expect(wrapper.vm.filteredBookingRecords).toHaveLength(2);
    expect(wrapper.vm.bookingPagination).toEqual(expect.objectContaining({ page: 1, page_size: 10 }));
    expect(wrapper.text()).not.toContain("受检者视角");
    expect(wrapper.text()).not.toContain("发起人视角");
  });

  it("机构选择每页显示六家并可翻页", async () => {
    mocks.fetchAppointmentAvailability.mockResolvedValue({
      data: {
        items: Array.from({ length: 7 }, (_, index) => ({
          institution: { id: index + 1, name: `机构 ${index + 1}`, branch_name: `分院 ${index + 1}` },
          packages: [],
          remaining: 10,
        })),
      },
    });
    const wrapper = mountView(AppointmentBookingView);
    await flushPromises();

    expect(wrapper.findAll(".booking-choice-card")).toHaveLength(6);
    wrapper.vm.availabilityPage = 2;
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll(".booking-choice-card")).toHaveLength(1);
    expect(wrapper.text()).toContain("机构 7");
  });

  it("restores appointment_date, institution and package after login", async () => {
    const appointmentDate = bookingDateBounds().minString;
    mocks.route.query = {
      appointment_date: appointmentDate,
      institution_id: "8",
      package_id: "9",
    };
    mocks.fetchAppointmentAvailability.mockResolvedValue({
      data: {
        items: [{
          institution: { id: 8, name: "澄心健康", branch_name: "徐汇院区" },
          packages: [{ id: 9, name: "综合体检", price: 999 }],
          remaining: 10,
        }],
      },
    });

    const wrapper = mountView(AppointmentBookingView);
    await flushPromises();

    expect(mocks.fetchAppointmentAvailability).toHaveBeenCalledWith(appointmentDate, "");
    expect(wrapper.vm.form.appointment_date).toBe(appointmentDate);
    expect(wrapper.vm.form.institution_id).toBe(8);
    expect(wrapper.vm.form.package_id).toBe(9);
  });

  it("shows only the approved health-code identity summary and keeps the token out of the page", async () => {
    const wrapper = mountView(AppointmentBookingView);
    await flushPromises();
    wrapper.vm.step = 3;
    wrapper.vm.healthIdInput = "HEALTH-ID-RAW";

    await wrapper.vm.resolveHealthIdParticipant();
    await flushPromises();

    expect(wrapper.text()).toContain("虚构受检者");
    expect(wrapper.text()).toContain("女性");
    expect(wrapper.text()).toContain("1992");
    expect(wrapper.text()).toContain("HE******01");
    expect(wrapper.text()).not.toContain("HEALTH-ID-RAW");
    expect(wrapper.text()).not.toContain("bpt-secret");
  });

  it("deduplicates health codes that belong to self or an active linked account", async () => {
    mocks.fetchFriends.mockResolvedValue({
      data: {
        items: [{
          id: 42,
          status: "active",
          booking_granted_to_me: true,
          counterparty: { id: 2, display_name: "虚构亲友" },
        }],
      },
    });
    const wrapper = mountView(AppointmentBookingView);
    await flushPromises();

    mocks.resolveBookingParticipantToken
      .mockResolvedValueOnce({
        data: { item: { participant_type: "linked_account", relation_id: 42 } },
      })
      .mockResolvedValueOnce({
        data: { item: { participant_type: "self" } },
      });

    wrapper.vm.healthIdInput = "HEALTH-LINKED";
    await wrapper.vm.resolveHealthIdParticipant();
    wrapper.vm.healthIdInput = "HEALTH-SELF";
    await wrapper.vm.resolveHealthIdParticipant();
    await flushPromises();

    expect(wrapper.vm.form.participant_keys).toEqual(["self:1", "relation:42"]);
    expect(wrapper.vm.tokenParticipants).toEqual([]);
    expect(wrapper.text()).not.toContain("HEALTH-LINKED");
    expect(wrapper.text()).not.toContain("HEALTH-SELF");
  });

  it("submits only canonical participants and discards consumed participant tokens after success", async () => {
    const wrapper = mountView(AppointmentBookingView);
    await flushPromises();
    wrapper.vm.healthIdInput = "HEALTH-ID-RAW";
    await wrapper.vm.resolveHealthIdParticipant();

    await wrapper.vm.book();
    await flushPromises();

    expect(mocks.createBookingGroup).toHaveBeenCalledWith(expect.objectContaining({
      participants: expect.arrayContaining([
        expect.objectContaining({
          type: "health_code_token",
          participant_token: "bpt-secret",
        }),
      ]),
    }));
    const submitted = mocks.createBookingGroup.mock.calls[0][0];
    expect(submitted).not.toHaveProperty("participant_user_ids");
    expect(submitted).not.toHaveProperty("participant_relation_ids");
    expect(submitted).not.toHaveProperty("participant_tokens");
    expect(submitted).not.toHaveProperty("participant_intakes");
    expect(wrapper.vm.tokenParticipants).toEqual([]);
    expect(wrapper.vm.form.participant_keys).toEqual(["self:1"]);
    expect(Object.keys(wrapper.vm.participantIntakes)).toEqual(["self:1"]);
    expect(wrapper.text()).not.toContain("bpt-secret");
  });

  it("submits the displayed self intake after switching from recent records to manual values", async () => {
    const wrapper = mountView(AppointmentBookingView);
    await flushPromises();

    wrapper.vm.setManualIntake("self:1", "height", true);
    wrapper.vm.setManualIntake("self:1", "weight", true);
    await wrapper.vm.book();

    expect(mocks.createBookingGroup).toHaveBeenCalledWith(expect.objectContaining({
      participants: [expect.objectContaining({
        type: "self",
        height_cm: 175,
        weight_kg: 72,
      })],
    }));
  });

  it("loads complaint history beyond the former first 50 and focuses a deep link", async () => {
    mocks.route.query = { complaint_id: "999" };
    const firstPage = Array.from({ length: 100 }, (_, index) => ({
      id: index + 1,
      appointment_id: 1000 + index,
      status: "resolved",
      category: "service",
      content: `虚构历史投诉 ${index + 1}`,
    }));
    mocks.fetchMyComplaints.mockImplementation(({ page }) => Promise.resolve({
      data: page === 1
        ? {
          items: firstPage,
          pagination: { page: 1, page_size: 100, total: 101, pages: 2 },
        }
        : {
          items: [{
            id: 999,
            appointment_id: 91,
            status: "platform_processing",
            category: "report",
            content: "虚构第 101 条深链投诉",
            institution: { name: "虚构体检机构" },
          }],
          pagination: { page: 2, page_size: 100, total: 101, pages: 2 },
        },
    }));

    const wrapper = mountView(AppointmentBookingView);
    await flushPromises();

    expect(mocks.fetchMyComplaints).toHaveBeenCalledWith({ page: 2, page_size: 100 });
    expect(wrapper.vm.complaintPagination).toEqual(expect.objectContaining({
      page: 11,
      total: 101,
      pages: 11,
    }));
    expect(wrapper.text()).toContain("虚构第 101 条深链投诉");
    expect(wrapper.vm.complaintForAppointment(91)).toEqual(expect.objectContaining({ id: 999 }));
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
