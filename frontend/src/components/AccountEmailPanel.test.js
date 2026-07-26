import { flushPromises, mount } from "@vue/test-utils";
import ElementPlus, { ElMessageBox } from "element-plus";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AccountEmailPanel from "./AccountEmailPanel.vue";
import { useAuthStore } from "../stores/auth";

const authApi = vi.hoisted(() => ({ changeAccountEmail: vi.fn() }));
vi.mock("../api/auth", () => authApi);

let wrapper;

beforeEach(() => {
  vi.clearAllMocks();
  const pinia = createPinia();
  setActivePinia(pinia);
  const store = useAuthStore(pinia);
  store.user = { id: 9, username: "institution1_staff1", role: "institution_admin", email: "old@example.test" };
  authApi.changeAccountEmail.mockResolvedValue({
    data: {
      message: "绑定邮箱已修改",
      user: { ...store.user, email: "new@example.test" },
    },
  });
  vi.spyOn(ElMessageBox, "confirm").mockResolvedValue("confirm");
  wrapper = mount(AccountEmailPanel, {
    props: { email: "old@example.test" },
    attachTo: document.body,
    global: { plugins: [pinia, ElementPlus], stubs: { teleport: true } },
  });
});

afterEach(() => {
  wrapper?.unmount();
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("账号邮箱改绑", () => {
  it("机构账号说明统一邮箱并在二次确认后更新", async () => {
    expect(wrapper.text()).toContain("账号与预约通知邮箱");
    expect(wrapper.text()).toContain("同一分院的所有管理员共用该邮箱");
    wrapper.vm.openDialog();
    wrapper.vm.newEmail = "new@example.test";
    await wrapper.vm.submit();
    await flushPromises();
    expect(ElMessageBox.confirm).toHaveBeenCalled();
    expect(authApi.changeAccountEmail).toHaveBeenCalledWith("new@example.test");
    expect(wrapper.emitted("changed")[0][0].email).toBe("new@example.test");
  });
});
