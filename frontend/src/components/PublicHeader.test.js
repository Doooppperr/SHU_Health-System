import { mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { createPinia } from "pinia";
import { afterEach, describe, expect, it, vi } from "vitest";

import PublicHeader from "./PublicHeader.vue";

const push = vi.fn();
vi.mock("vue-router", () => ({
  useRouter: () => ({ push }),
}));

const wrappers = [];
afterEach(() => wrappers.splice(0).forEach((wrapper) => wrapper.unmount()));

describe("PublicHeader", () => {
  it("uses one centered public navigation contract for home and catalog pages", () => {
    const wrapper = mount(PublicHeader, {
      global: {
        plugins: [createPinia(), ElementPlus],
        stubs: {
          RouterLink: { props: ["to"], template: '<a :data-to="JSON.stringify(to)"><slot /></a>' },
          AppearanceQuickControls: { template: "<div data-testid='appearance-controls' />" },
        },
      },
    });
    wrappers.push(wrapper);
    expect(wrapper.classes()).toContain("public-site-header");
    expect(wrapper.get("nav").attributes("aria-label")).toBe("公开页面导航");
    expect(wrapper.get("nav").text()).toContain("机构与套餐");
    expect(wrapper.get("nav").text()).toContain("核心能力");
    expect(wrapper.get("nav").text()).toContain("加入我们");
    expect(wrapper.find('[data-testid="appearance-controls"]').exists()).toBe(true);
  });
});
