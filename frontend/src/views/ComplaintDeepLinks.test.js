import { flushPromises, mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  route: { query: {} },
  fetchOrgComplaints: vi.fn(),
  fetchAdminComplaints: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRoute: () => mocks.route,
}));

vi.mock("../api/complaints", () => ({
  fetchOrgComplaints: mocks.fetchOrgComplaints,
  replyOrgComplaint: vi.fn(),
  fetchAdminComplaints: mocks.fetchAdminComplaints,
  startAdminComplaint: vi.fn(),
  replyAdminComplaint: vi.fn(),
  resolveAdminComplaint: vi.fn(),
}));

import AdminComplaintsView from "./admin/AdminComplaintsView.vue";
import OrgComplaintsView from "./org/OrgComplaintsView.vue";

const wrappers = [];

function mountView(component) {
  const wrapper = mount(component, {
    attachTo: document.body,
    global: {
      plugins: [ElementPlus],
      stubs: { teleport: true },
    },
  });
  wrappers.push(wrapper);
  return wrapper;
}

function complaint(id, content) {
  return {
    id,
    status: "institution_pending",
    category: "service",
    category_label: "服务态度",
    content,
    created_at: "2026-07-30T08:00:00Z",
    appointment_id: id + 1000,
    appointment: { id: id + 1000, subject_name: "虚构受检者" },
    institution: { name: "虚构体检机构" },
    events: [],
    messages: [],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.route.query = {};
  HTMLElement.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  document.body.innerHTML = "";
});

describe("投诉通知深链", () => {
  it("finds an institution complaint on a later server page", async () => {
    mocks.route.query = { complaint_id: "93", status: "institution_pending" };
    mocks.fetchOrgComplaints.mockImplementation(({ page }) => Promise.resolve({
      data: {
        items: page === 3
          ? [complaint(93, "虚构机构端第三页投诉")]
          : [complaint(page, `虚构机构端第 ${page} 页投诉`)],
        pagination: { page, page_size: 15, total: 31, pages: 3 },
      },
    }));

    const wrapper = mountView(OrgComplaintsView);
    await flushPromises();

    expect(mocks.fetchOrgComplaints).toHaveBeenCalledWith({ page: 3, page_size: 15 });
    expect(wrapper.vm.pagination.page).toBe(3);
    expect(wrapper.text()).toContain("虚构机构端第三页投诉");
    expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("finds an administrator complaint on a later server page and opens it", async () => {
    mocks.route.query = { complaint_id: "82" };
    mocks.fetchAdminComplaints.mockImplementation(({ page }) => Promise.resolve({
      data: {
        items: page === 2
          ? [complaint(82, "虚构平台端第二页投诉")]
          : [complaint(page, `虚构平台端第 ${page} 页投诉`)],
        pagination: { page, page_size: 15, total: 16, pages: 2 },
      },
    }));

    const wrapper = mountView(AdminComplaintsView);
    await flushPromises();

    expect(mocks.fetchAdminComplaints).toHaveBeenCalledWith({ page: 2, page_size: 15 });
    expect(wrapper.vm.pagination.page).toBe(2);
    expect(wrapper.vm.current.id).toBe(82);
    expect(wrapper.vm.detailVisible).toBe(true);
    expect(wrapper.text()).toContain("虚构平台端第二页投诉");
  });
});
