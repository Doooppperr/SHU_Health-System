import { flushPromises, mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { createPinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  fetchOrgInstitution: vi.fn(),
  updateOrgInstitution: vi.fn(),
  deactivateOrgAccount: vi.fn(),
  fetchOrgAccountDeactivationCheck: vi.fn(),
}));

vi.mock("../../api/org", () => api);
vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

import OrgProfileView from "./OrgProfileView.vue";

const institution = {
  organization: { name: "澄心健康管理中心" },
  branch_name: "徐汇综合院区",
  district: "徐汇区",
  address: "斜土路1609号",
  metro_info: "4号线",
  consult_phone: "021-64031188",
  ext: "101",
  closed_day: "周一休",
  description: "原简介",
  identity_locked: true,
};

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchOrgInstitution.mockResolvedValue({ data: { item: institution } });
  api.updateOrgInstitution.mockImplementation((payload) => Promise.resolve({
    data: { item: { ...institution, ...payload } },
  }));
});

describe("OrgProfileView", () => {
  it("locks institution identity and submits only editable public fields", async () => {
    const wrapper = mount(OrgProfileView, {
      global: {
        plugins: [createPinia(), ElementPlus],
        stubs: {
          AccountEmailPanel: true,
          AccountSecurityPanel: true,
          OrgGalleryView: true,
          teleport: true,
        },
      },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("机构身份信息已锁定");
    const lockedValues = new Set([
      "澄心健康管理中心", "徐汇综合院区", "徐汇区", "斜土路1609号",
    ]);
    const lockedInputs = wrapper.findAllComponents({ name: "ElInput" })
      .filter((item) => lockedValues.has(item.props("modelValue")));
    expect(lockedInputs).toHaveLength(4);
    expect(lockedInputs.every((item) => item.props("disabled") === true)).toBe(true);

    wrapper.vm.form.consult_phone = "021-64030000";
    wrapper.vm.form.description = "更新后的公开简介";
    await wrapper.vm.save();
    expect(api.updateOrgInstitution).toHaveBeenCalledWith({
      metro_info: "4号线",
      consult_phone: "021-64030000",
      ext: "101",
      closed_day: "周一休",
      description: "更新后的公开简介",
    });
    expect(api.updateOrgInstitution.mock.calls[0][0]).not.toHaveProperty("branch_name");
    expect(api.updateOrgInstitution.mock.calls[0][0]).not.toHaveProperty("district");
    expect(api.updateOrgInstitution.mock.calls[0][0]).not.toHaveProperty("address");
  });
});
