import { flushPromises, mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  fetchAiRecords: vi.fn(),
  streamAiChat: vi.fn(),
  streamAiAnalysis: vi.fn(),
}));

vi.mock("../api/ai", () => api);

import AiAssistant from "./AiAssistant.vue";
import { useAiChatStore } from "../stores/aiChat";
import { useAuthStore } from "../stores/auth";

const wrappers = [];
const originalInnerWidth = window.innerWidth;
const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
let scrollIntoViewMock;
const record = {
  id: 9,
  owner_id: 10,
  owner: { username: "测试用户" },
  exam_date: "2026-07-01",
  institution: { name: "测试医院" },
  indicator_count: 4,
};

function completeStream(_payload, { onEvent }) {
  onEvent({ event: "status", stage: "provider", message: "正在分析" });
  onEvent({ event: "delta", text: "分析结果" });
  onEvent({ event: "done", decision: "answer", source: "model" });
  return Promise.resolve();
}

function mountAssistant({ authenticated = true, overlayMode = false } = {}) {
  const pinia = createPinia();
  setActivePinia(pinia);
  const authStore = useAuthStore(pinia);
  if (authenticated) {
    authStore.accessToken = "token";
    authStore.refreshToken = "refresh";
    authStore.user = { id: 10, username: "tester", role: "user" };
  }
  const aiStore = useAiChatStore(pinia);
  aiStore.initialize(authenticated ? 10 : null);
  aiStore.setOpen(true);
  const wrapper = mount(AiAssistant, {
    attachTo: document.body,
    props: { overlayMode },
    global: { plugins: [pinia, ElementPlus] },
  });
  wrappers.push(wrapper);
  return { wrapper, aiStore };
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    writable: true,
    value: originalInnerWidth,
  });
  scrollIntoViewMock = vi.fn();
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    writable: true,
    value: scrollIntoViewMock,
  });
  api.fetchAiRecords.mockReset();
  api.streamAiChat.mockReset();
  api.streamAiAnalysis.mockReset();
  api.fetchAiRecords.mockResolvedValue({ data: { items: [record] } });
  api.streamAiChat.mockImplementation(completeStream);
  api.streamAiAnalysis.mockImplementation(completeStream);
});

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  document.body.innerHTML = "";
  vi.restoreAllMocks();
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    writable: true,
    value: originalInnerWidth,
  });
  if (originalScrollIntoView) {
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      writable: true,
      value: originalScrollIntoView,
    });
  } else {
    delete HTMLElement.prototype.scrollIntoView;
  }
});

describe("AiAssistant record-aware interaction", () => {
  it("uses modal focus behavior and hides resizing in dynamic overlay mode", async () => {
    const { wrapper } = mountAssistant({ overlayMode: true });
    await flushPromises();

    const panel = wrapper.get("#ai-chat-panel");
    expect(panel.attributes("role")).toBe("dialog");
    expect(panel.attributes("aria-modal")).toBe("true");
    expect(wrapper.find(".ai-resize-handle").exists()).toBe(false);
    expect(wrapper.vm.panelOverlayMode).toBe(true);
  });

  it("moves external focus into an open panel when overlay mode turns on", async () => {
    const { wrapper } = mountAssistant({ overlayMode: false });
    await flushPromises();

    const externalButton = document.createElement("button");
    document.body.appendChild(externalButton);
    externalButton.focus();
    expect(document.activeElement).toBe(externalButton);

    await wrapper.setProps({ overlayMode: true });
    await flushPromises();

    const panel = wrapper.get("#ai-chat-panel");
    expect(panel.attributes("role")).toBe("dialog");
    expect(panel.element.contains(document.activeElement)).toBe(true);
  });

  it("does not load or show records initially and loads them on manual request", async () => {
    const { wrapper } = mountAssistant();
    await flushPromises();

    expect(api.fetchAiRecords).not.toHaveBeenCalled();
    expect(wrapper.find('[data-testid="manual-record-picker"]').exists()).toBe(false);

    await wrapper.get('[data-testid="open-record-picker"]').trigger("click");
    await flushPromises();
    expect(api.fetchAiRecords).toHaveBeenCalledOnce();
    const picker = wrapper.get('[data-testid="manual-record-picker"]');
    expect(wrapper.text()).toContain("引用档案到下一条消息");
    expect(scrollIntoViewMock).toHaveBeenCalledWith({
      block: "nearest",
      behavior: "smooth",
    });
    expect(document.activeElement).toBe(picker.element);
  });

  it("shows the analysis confirmation card and starts only after one-time consent", async () => {
    const { wrapper, aiStore } = mountAssistant();
    aiStore.prepareRecordAnalysis([record]);
    await flushPromises();

    const card = wrapper.get('[data-testid="analysis-confirmation"]');
    expect(card.text()).toContain("1 份档案");
    expect(card.text()).toContain("2026-07-01 至 2026-07-01");
    expect(document.activeElement).toBe(card.element);
    expect(wrapper.get('[data-testid="start-analysis"]').attributes("disabled")).toBeDefined();

    aiStore.setConsentGiven(true);
    await wrapper.vm.$nextTick();
    await wrapper.get('[data-testid="start-analysis"]').trigger("click");
    await flushPromises();

    expect(api.streamAiAnalysis).toHaveBeenCalledWith(
      { selected_record_ids: [record.id], consent: true },
      expect.any(Object)
    );
    expect(wrapper.text()).toContain("分析结果");
    expect(aiStore.consentGiven).toBe(false);

    await wrapper.get('[data-testid="follow-up-records"]').trigger("click");
    await flushPromises();
    expect(aiStore.selectedRecordIds).toEqual([record.id]);
    expect(aiStore.consentGiven).toBe(false);
    expect(wrapper.find('[data-testid="manual-record-picker"]').exists()).toBe(true);
  });

  it("places a prepared analysis after existing history and scrolls it into view", async () => {
    const { wrapper, aiStore } = mountAssistant();
    aiStore.messages = Array.from({ length: 12 }, (_, index) => ({
      id: `message-${index}`,
      role: index % 2 === 0 ? "user" : "assistant",
      content: `历史消息 ${index + 1}`,
      decision: "answer",
    }));

    aiStore.prepareRecordAnalysis([record]);
    await flushPromises();

    const rows = wrapper.findAll(".ai-message-row");
    const card = wrapper.get('[data-testid="analysis-confirmation"]');
    expect(rows.at(-1).element.nextElementSibling).toBe(card.element);
    expect(scrollIntoViewMock).toHaveBeenCalledWith({
      block: "nearest",
      behavior: "smooth",
    });
  });

  it("renders a requested record picker inside the corresponding assistant message", async () => {
    const { wrapper, aiStore } = mountAssistant();
    aiStore.messages = [
      { id: "user-1", role: "user", content: "分析我的趋势" },
      {
        id: "assistant-1",
        role: "assistant",
        content: "需要先选择档案。",
        action: "select_records",
        decision: "answer",
      },
    ];
    aiStore.showRecordPicker({
      assistantId: "assistant-1",
      query: "分析我的趋势",
      mode: "action",
    });
    await flushPromises();

    const picker = wrapper.get('[data-testid="action-record-picker"]');
    expect(picker.element.closest(".ai-message-bubble")).not.toBeNull();
    expect(picker.text()).toContain("选择本次引用的档案");
    expect(scrollIntoViewMock).toHaveBeenCalledWith({
      block: "nearest",
      behavior: "smooth",
    });
    expect(document.activeElement).toBe(picker.element);
  });

  it("ignores Enter while an IME is composing or reports legacy keyCode 229", async () => {
    const { wrapper } = mountAssistant();
    const textarea = wrapper.get("textarea");
    await textarea.setValue("输入法候选内容");

    const composingEnter = new KeyboardEvent("keydown", {
      key: "Enter",
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(composingEnter, "isComposing", { value: true });
    textarea.element.dispatchEvent(composingEnter);

    const legacyImeEnter = new KeyboardEvent("keydown", {
      key: "Enter",
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(legacyImeEnter, "keyCode", { value: 229 });
    textarea.element.dispatchEvent(legacyImeEnter);
    await flushPromises();

    expect(api.streamAiChat).not.toHaveBeenCalled();
    expect(textarea.element.value).toBe("输入法候选内容");

    await textarea.trigger("keydown", { key: "Enter" });
    await flushPromises();
    expect(api.streamAiChat).toHaveBeenCalledOnce();
  });

  it("clamps and persists a restored panel width on a narrow desktop", async () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      writable: true,
      value: 900,
    });
    localStorage.setItem("health-ai-panel-width", "720");

    const { aiStore } = mountAssistant();
    await flushPromises();

    expect(aiStore.panelWidth).toBe(495);
    expect(localStorage.getItem("health-ai-panel-width")).toBe("495");
  });

  it("does not overwrite the saved desktop panel width on a compact viewport", async () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      writable: true,
      value: 600,
    });
    localStorage.setItem("health-ai-panel-width", "720");

    const { aiStore } = mountAssistant();
    await flushPromises();

    expect(aiStore.panelWidth).toBe(720);
    expect(localStorage.getItem("health-ai-panel-width")).toBe("720");
  });

  it("renders optimistic messages, exposes cancel, and keeps a retryable failure", async () => {
    api.streamAiChat.mockImplementation(
      (_payload, { signal, onEvent }) =>
        new Promise((_resolve, reject) => {
          onEvent({ event: "status", message: "正在连接 AI" });
          signal.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError"))
          );
        })
    );
    const { wrapper } = mountAssistant();
    await wrapper.get("textarea").setValue("请解释这个指标");
    await wrapper.get('[data-testid="send-message"]').trigger("click");
    await wrapper.vm.$nextTick();

    expect(wrapper.text()).toContain("请解释这个指标");
    expect(wrapper.text()).toContain("正在连接 AI");
    await wrapper.get('[data-testid="cancel-stream"]').trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("已取消本次生成");
    expect(wrapper.find('[data-testid="retry-message"]').exists()).toBe(true);
  });

  it("renders every streamed delta before the request finishes", async () => {
    let emit;
    let finish;
    api.streamAiChat.mockImplementation(
      (_payload, options) =>
        new Promise((resolve) => {
          emit = options.onEvent;
          finish = resolve;
        })
    );
    const { wrapper } = mountAssistant();
    await wrapper.get("textarea").setValue("逐步回复");
    await wrapper.get('[data-testid="send-message"]').trigger("click");
    await wrapper.vm.$nextTick();

    emit({ event: "delta", text: "第一段" });
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("第一段");

    emit({ event: "delta", text: "，第二段" });
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("第一段，第二段");

    emit({ event: "done", decision: "answer", source: "model" });
    finish();
    await flushPromises();
  });

  it("does not route a non-retryable analysis failure through chat retry", async () => {
    const { wrapper, aiStore } = mountAssistant();
    aiStore.messages = [
      { id: "request", role: "user", content: "智能分析 1 份档案", kind: "analysis-request" },
      {
        id: "failed-analysis",
        role: "assistant",
        content: "本次回复未完成。",
        kind: "analysis",
        failed: true,
        retryable: false,
        retryRecords: [record],
        errorMessage: "档案不可用",
      },
    ];
    await wrapper.vm.$nextTick();

    expect(wrapper.find('[data-testid="retry-analysis"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="retry-message"]').exists()).toBe(false);
  });
});
