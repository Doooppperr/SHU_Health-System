import { flushPromises, mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const adminApi = vi.hoisted(() => ({
  createAdminOrganization: vi.fn(),
  createAdminInstitution: vi.fn(),
  createAdminPackage: vi.fn(),
  deactivateAdminInstitution: vi.fn(),
  deactivateAdminPackage: vi.fn(),
  deleteAdminImage: vi.fn(),
  fetchAdminImages: vi.fn(),
  fetchAdminInstitutionAccount: vi.fn(),
  fetchAdminInstitutions: vi.fn(),
  fetchAdminOrganizations: vi.fn(),
  fetchAdminPackages: vi.fn(),
  reorderAdminImages: vi.fn(),
  resetAdminInstitutionAccount: vi.fn(),
  restoreAdminInstitution: vi.fn(),
  retryAdminInstitutionAccountNotification: vi.fn(),
  updateAdminInstitution: vi.fn(),
  updateAdminPackage: vi.fn(),
  uploadAdminImage: vi.fn(),
}));

vi.mock("../../api/admin", () => adminApi);

import AdminInstitutionsView from "./AdminInstitutionsView.vue";

const institution = {
  id: 17,
  organization_id: 3,
  organization: { id: 3, name: "虚构澄心健康集团" },
  name: "虚构澄心健康集团",
  branch_name: "虚构徐汇综合院区",
  district: "徐汇区",
  address: "虚构测试路 17 号",
  package_count: 3,
  total_package_count: 3,
  administrator_count: 1,
  is_active: true,
};

const accountResponse = (status = "failed") => ({
  data: {
    account: {
      id: 81,
      username: "org_xuhui_test",
      email: "org-xuhui@example.test",
      must_change_initial_password: true,
    },
    delivery: {
      status,
      recipient: "org-xuhui@example.test",
      attempts: status === "failed" ? 2 : 3,
      sent_at: status === "sent" ? "2026-08-03T09:30:00+08:00" : null,
      sensitive_payload_cleared_at: status === "sent" ? "2026-08-03T09:30:01+08:00" : null,
    },
  },
});

const wrappers = [];
const OverlayStub = {
  props: ["modelValue", "title"],
  emits: ["update:modelValue"],
  template: `
    <section v-if="modelValue" class="overlay-stub">
      <h3>{{ title }}</h3>
      <slot />
      <slot name="footer" />
    </section>
  `,
};

function findButton(wrapper, label) {
  return wrapper.findAll("button").find((button) => button.text().trim() === label);
}

async function mountView() {
  const wrapper = mount(AdminInstitutionsView, {
    attachTo: document.body,
    global: {
      plugins: [ElementPlus],
      stubs: {
        teleport: true,
        ElDialog: OverlayStub,
        ElDrawer: OverlayStub,
      },
    },
  });
  wrappers.push(wrapper);
  await flushPromises();
  return wrapper;
}

async function openAccountDrawer(wrapper) {
  const button = findButton(wrapper, "账号与邮件");
  expect(button).toBeTruthy();
  await button.trigger("click");
  await flushPromises();
}

beforeEach(() => {
  vi.clearAllMocks();
  adminApi.fetchAdminInstitutions.mockResolvedValue({
    data: {
      items: [institution],
      pagination: { page: 1, page_size: 15, total: 1, pages: 1 },
    },
  });
  adminApi.fetchAdminOrganizations.mockResolvedValue({
    data: { items: [{ id: 3, name: "虚构澄心健康集团" }] },
  });
  adminApi.fetchAdminInstitutionAccount.mockResolvedValue(accountResponse());
  adminApi.retryAdminInstitutionAccountNotification.mockResolvedValue({
    data: {
      message: "账号通知已重新进入发送队列",
      delivery: { status: "pending", attempts: 3 },
    },
  });
  adminApi.resetAdminInstitutionAccount.mockResolvedValue({
    data: {
      message: "机构账号已重置并进入发送队列",
      account: {
        username: "org_xuhui_test",
        email: "new-org-xuhui@example.test",
        initial_password: "ResetPass!2026",
      },
      delivery: { status: "pending" },
    },
  });
  adminApi.createAdminInstitution.mockResolvedValue({
    data: {
      account: {
        username: "org_new_branch",
        email: "new-branch@example.test",
        initial_password: "CreatePass!2026",
      },
      delivery: { status: "pending" },
    },
  });
});

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  document.body.innerHTML = "";
});

describe("管理员机构账号与邮件管理", () => {
  it("从机构行加载账号与邮件抽屉，并展示凭据交付状态", async () => {
    const wrapper = await mountView();

    await openAccountDrawer(wrapper);

    expect(adminApi.fetchAdminInstitutionAccount).toHaveBeenCalledWith(17);
    expect(wrapper.vm.accountDrawerVisible).toBe(true);
    expect(wrapper.text()).toContain("org_xuhui_test");
    expect(wrapper.text()).toContain("org-xuhui@example.test");
    expect(wrapper.text()).toContain("仍需修改初始密码");
    expect(wrapper.text()).toContain("发送失败");
    expect(wrapper.text()).toContain("2");
  });

  it("允许对失败的凭据邮件执行重试并重新加载状态", async () => {
    adminApi.fetchAdminInstitutionAccount
      .mockResolvedValueOnce(accountResponse("failed"))
      .mockResolvedValueOnce(accountResponse("pending"));
    const wrapper = await mountView();
    await openAccountDrawer(wrapper);

    const retryButton = findButton(wrapper, "重试发送");
    expect(retryButton).toBeTruthy();
    expect(retryButton.attributes("disabled")).toBeUndefined();
    await retryButton.trigger("click");
    await flushPromises();

    expect(adminApi.retryAdminInstitutionAccountNotification).toHaveBeenCalledWith(17);
    expect(adminApi.fetchAdminInstitutionAccount).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain("等待发送");
  });

  it("通过重置弹窗提交新邮箱和新初始密码", async () => {
    const wrapper = await mountView();
    await openAccountDrawer(wrapper);

    const openResetButton = findButton(wrapper, "重置密码并重新发送");
    expect(openResetButton).toBeTruthy();
    await openResetButton.trigger("click");
    await flushPromises();

    expect(wrapper.vm.accountResetVisible).toBe(true);
    expect(wrapper.vm.accountResetForm.email).toBe("org-xuhui@example.test");
    wrapper.vm.accountResetForm.email = "new-org-xuhui@example.test";
    wrapper.vm.accountResetForm.password = "ResetPass!2026";
    await wrapper.vm.$nextTick();

    const confirmButton = findButton(wrapper, "确认重置并发送");
    expect(confirmButton).toBeTruthy();
    await confirmButton.trigger("click");
    await flushPromises();

    expect(adminApi.resetAdminInstitutionAccount).toHaveBeenCalledWith(17, {
      email: "new-org-xuhui@example.test",
      password: "ResetPass!2026",
    });
    expect(wrapper.vm.accountResetVisible).toBe(false);
    expect(wrapper.vm.accountResultVisible).toBe(true);
    expect(wrapper.text()).toContain("ResetPass!2026");
  });

  it("创建分院时拦截缺失账密，并完整提交 username、password、email", async () => {
    const wrapper = await mountView();

    const createButton = findButton(wrapper, "新增分院");
    expect(createButton).toBeTruthy();
    await createButton.trigger("click");
    await flushPromises();

    Object.assign(wrapper.vm.institutionForm, {
      organization_id: 3,
      branch_name: "虚构虹桥分院",
      district: "长宁区",
      address: "虚构验收路 99 号",
      username: "",
      password: "",
      email: "",
    });
    await wrapper.vm.$nextTick();

    let saveButton = findButton(wrapper, "保存");
    expect(saveButton).toBeTruthy();
    await saveButton.trigger("click");
    await flushPromises();
    expect(adminApi.createAdminInstitution).not.toHaveBeenCalled();

    Object.assign(wrapper.vm.institutionForm, {
      username: "org_new_branch",
      password: "CreatePass!2026",
      email: "new-branch@example.test",
    });
    await wrapper.vm.$nextTick();
    saveButton = findButton(wrapper, "保存");
    await saveButton.trigger("click");
    await flushPromises();

    expect(adminApi.createAdminInstitution).toHaveBeenCalledWith(expect.objectContaining({
      organization_id: 3,
      branch_name: "虚构虹桥分院",
      district: "长宁区",
      address: "虚构验收路 99 号",
      username: "org_new_branch",
      password: "CreatePass!2026",
      email: "new-branch@example.test",
    }));
    expect(wrapper.vm.accountResultVisible).toBe(true);
  });
});
