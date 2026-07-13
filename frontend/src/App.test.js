import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("vue-router", () => ({
  useRoute: () => ({ name: "public-home", fullPath: "/" }),
}));

import App from "./App.vue";
import { useAiChatStore } from "./stores/aiChat";

const wrappers = [];
const originalInnerWidth = window.innerWidth;
const originalInnerHeight = window.innerHeight;
const originalClientWidth = document.documentElement.clientWidth;

function setViewport(width, height, clientWidth = width) {
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    writable: true,
    value: width,
  });
  Object.defineProperty(window, "innerHeight", {
    configurable: true,
    writable: true,
    value: height,
  });
  Object.defineProperty(document.documentElement, "clientWidth", {
    configurable: true,
    value: clientWidth,
  });
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  setViewport(1600, 900);
});

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  setViewport(originalInnerWidth, originalInnerHeight, originalClientWidth);
});

describe("App AI panel layout", () => {
  it("publishes a scaled desktop canvas and resets it for compact overlay", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = mount(App, {
      global: {
        plugins: [pinia],
        stubs: {
          AiAssistant: true,
          RouterView: { template: '<main id="main-content" tabindex="-1" />' },
        },
      },
    });
    wrappers.push(wrapper);

    const aiStore = useAiChatStore(pinia);
    aiStore.setPanelWidth(560);
    aiStore.setOpen(true);
    await wrapper.vm.$nextTick();

    const shell = wrapper.get(".app-with-ai");
    const stage = wrapper.get(".app-route-stage");
    expect(shell.classes()).toContain("ai-panel-active");
    expect(stage.attributes("inert")).toBeUndefined();
    expect(shell.element.style.getPropertyValue("--ai-panel-width")).toBe("560px");
    expect(shell.element.style.getPropertyValue("--ai-stage-design-width")).toBe(
      "1440px"
    );
    expect(Number(shell.element.style.getPropertyValue("--ai-stage-scale"))).toBeCloseTo(
      1040 / 1440
    );

    setViewport(860, 700);
    window.dispatchEvent(new Event("resize"));
    await wrapper.vm.$nextTick();

    expect(shell.element.style.getPropertyValue("--ai-stage-design-width")).toBe(
      "860px"
    );
    expect(shell.element.style.getPropertyValue("--ai-stage-scale")).toBe("1");
    expect(stage.attributes("inert")).toBe("");
    expect(stage.attributes("aria-hidden")).toBe("true");
  });

  it("uses the content viewport width so the page does not overlap the panel scrollbar", async () => {
    setViewport(1280, 720, 1265);
    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = mount(App, {
      global: {
        plugins: [pinia],
        stubs: {
          AiAssistant: true,
          RouterView: { template: '<main id="main-content" tabindex="-1" />' },
        },
      },
    });
    wrappers.push(wrapper);

    const aiStore = useAiChatStore(pinia);
    aiStore.setPanelWidth(360);
    aiStore.setOpen(true);
    await wrapper.vm.$nextTick();

    const shell = wrapper.get(".app-with-ai");
    expect(shell.element.style.getPropertyValue("--ai-stage-design-width")).toBe(
      "1265px"
    );
    expect(Number(shell.element.style.getPropertyValue("--ai-stage-scale"))).toBeCloseTo(
      905 / 1265
    );
  });
});
