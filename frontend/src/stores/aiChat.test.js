import { flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  fetchAiRecords: vi.fn(),
  streamAiChat: vi.fn(),
  streamAiAnalysis: vi.fn(),
}));

vi.mock("../api/ai", () => api);

import { useAiChatStore } from "./aiChat";
import { AI_SESSION_PREFIX } from "../utils/aiSession";

const record = {
  id: 7,
  owner_id: 10,
  owner: { username: "测试用户" },
  exam_date: "2026-06-01",
  institution: { name: "测试医院" },
  indicator_count: 3,
};

function emitDone(options, extra = {}) {
  options.onEvent({ event: "meta", request_id: "req-1", model: "deepseek" });
  options.onEvent({ event: "delta", text: "已完成" });
  options.onEvent({ event: "done", decision: "answer", source: "model", ...extra });
}

beforeEach(() => {
  setActivePinia(createPinia());
  localStorage.clear();
  sessionStorage.clear();
  api.fetchAiRecords.mockReset();
  api.streamAiChat.mockReset();
  api.streamAiAnalysis.mockReset();
  api.fetchAiRecords.mockResolvedValue({ data: { items: [record] } });
  api.streamAiChat.mockImplementation(async (_payload, options) => emitDone(options));
  api.streamAiAnalysis.mockImplementation(async (_payload, options) => emitDone(options));
});

describe("AI chat store", () => {
  it("does not load record metadata until a picker is explicitly opened", async () => {
    const store = useAiChatStore();
    store.initialize(10);
    store.setOpen(true);
    await flushPromises();

    expect(api.fetchAiRecords).not.toHaveBeenCalled();
    store.showRecordPicker({ mode: "manual" });
    await flushPromises();
    expect(api.fetchAiRecords).toHaveBeenCalledOnce();
  });

  it("clears record metadata on identity changes and ignores a stale load", async () => {
    let resolveRecords;
    api.fetchAiRecords.mockReturnValue(
      new Promise((resolve) => {
        resolveRecords = resolve;
      })
    );
    const store = useAiChatStore();
    store.initialize(10);
    store.showRecordPicker({ mode: "manual" });
    expect(store.recordsLoading).toBe(true);

    store.switchIdentity(20);
    expect(store.availableRecords).toEqual([]);
    expect(store.recordsLoaded).toBe(false);
    expect(store.recordsLoading).toBe(false);

    resolveRecords({ data: { items: [record] } });
    await flushPromises();
    expect(store.availableRecords).toEqual([]);
    expect(store.recordsLoaded).toBe(false);
  });

  it("keeps a confirmed manual selection for exactly the next message", async () => {
    const store = useAiChatStore();
    store.initialize(10);
    store.showRecordPicker({ mode: "manual" });
    await flushPromises();
    store.setSelectedRecordIds([record.id]);
    store.setConsentGiven(true);

    await store.confirmRecordPicker(true);
    expect(store.pickerContext).toBeNull();
    expect(store.selectedRecordIds).toEqual([record.id]);
    expect(store.consentGiven).toBe(true);

    await store.sendMessage("结合档案解释", true);
    expect(api.streamAiChat).toHaveBeenCalledWith(
      expect.objectContaining({ selected_record_ids: [record.id], consent: true }),
      expect.any(Object)
    );
    expect(store.selectedRecordIds).toEqual([]);
    expect(store.consentGiven).toBe(false);
  });

  it("sends an owner-wide scope once and resets its consent", async () => {
    api.fetchAiRecords.mockResolvedValueOnce({
      data: {
        items: [record],
        owners: [
          {
            owner_id: 10,
            owner: { username: "测试用户", label: "本人" },
            record_count: 20,
            date_range: { first: "2021-09-15", latest: "2026-06-15" },
          },
        ],
      },
    });
    const store = useAiChatStore();
    store.initialize(10);
    store.showRecordPicker({ mode: "manual" });
    await flushPromises();
    store.setRecordSelectionMode("owner");
    store.setSelectedOwnerId(10);
    store.setConsentGiven(true);

    await store.confirmRecordPicker(true);
    await store.sendMessage("分析全部历史档案", true);

    const payload = api.streamAiChat.mock.calls[0][0];
    expect(payload.record_scope).toEqual({ owner_id: 10, mode: "all_confirmed" });
    expect(payload.selected_record_ids).toBeUndefined();
    expect(payload.consent).toBe(true);
    expect(store.selectedOwnerId).toBeNull();
    expect(store.consentGiven).toBe(false);
  });

  it("opens an action picker while streaming and reuses the original message pair", async () => {
    api.streamAiChat
      .mockImplementationOnce(async (_payload, options) => {
        options.onEvent({ event: "action", action: "select_records", message: "请选择档案" });
        options.onEvent({ event: "done", decision: "answer", source: "rule" });
      })
      .mockImplementationOnce(async (_payload, options) => emitDone(options));
    const store = useAiChatStore();
    store.initialize(10);

    await store.sendMessage("分析我的历史趋势", true);
    await flushPromises();
    expect(store.messages).toHaveLength(2);
    expect(store.pickerContext).toMatchObject({
      assistantId: store.messages[1].id,
      mode: "action",
    });
    expect(api.fetchAiRecords).toHaveBeenCalledOnce();

    store.setSelectedRecordIds([record.id]);
    store.setConsentGiven(true);
    await store.confirmRecordPicker(true);

    expect(store.messages).toHaveLength(2);
    expect(store.messages[0].content).toBe("分析我的历史趋势");
    expect(store.messages[1].content).toBe("已完成");
    expect(api.streamAiChat.mock.calls[1][0]).toMatchObject({
      message: "分析我的历史趋势",
      selected_record_ids: [record.id],
      consent: true,
    });
  });

  it("retains retry records after a failed analysis and can prepare them again", async () => {
    const failure = Object.assign(new Error("生成超时"), { retryable: true });
    api.streamAiAnalysis.mockRejectedValueOnce(failure);
    const store = useAiChatStore();
    store.initialize(10);
    expect(store.prepareRecordAnalysis([record])).toBe(true);
    store.setConsentGiven(true);

    const assistant = await store.analyzePreparedRecords();
    expect(assistant.failed).toBe(true);
    expect(assistant.retryRecords).toHaveLength(1);
    expect(store.preparedAnalysis).toBeNull();

    expect(store.retryAnalysis(assistant)).toBe(true);
    expect(store.preparedAnalysis).toMatchObject({ ownerId: 10 });
    expect(store.selectedRecordIds).toEqual([record.id]);
    expect(store.consentGiven).toBe(false);
  });

  it("requires fresh record consent before retrying a failed record-aware chat", async () => {
    const failure = Object.assign(new Error("连接中断"), { retryable: true });
    api.streamAiChat
      .mockRejectedValueOnce(failure)
      .mockImplementationOnce(async (_payload, options) => emitDone(options));
    const store = useAiChatStore();
    store.initialize(10);
    store.showRecordPicker({ mode: "manual" });
    await flushPromises();
    store.setSelectedRecordIds([record.id]);
    store.setConsentGiven(true);
    await store.confirmRecordPicker(true);

    const failedAssistant = await store.sendMessage("这些指标是什么意思", true);
    expect(failedAssistant.retryRecordIds).toEqual([record.id]);
    expect(store.selectedRecordIds).toEqual([]);

    await store.retryMessage(failedAssistant.id, true);
    await flushPromises();
    expect(api.streamAiChat).toHaveBeenCalledOnce();
    expect(store.pickerContext).toMatchObject({
      assistantId: failedAssistant.id,
      mode: "action",
    });
    expect(store.selectedRecordIds).toEqual([record.id]);
    expect(store.consentGiven).toBe(false);

    store.setConsentGiven(true);
    await store.confirmRecordPicker(true);
    expect(api.streamAiChat).toHaveBeenCalledTimes(2);
    expect(api.streamAiChat.mock.calls[1][0]).toMatchObject({
      selected_record_ids: [record.id],
      consent: true,
    });
  });

  it("does not let an old generic retry consume analysis consent", async () => {
    const failure = Object.assign(new Error("连接中断"), { retryable: true });
    api.streamAiChat.mockRejectedValueOnce(failure);
    const store = useAiChatStore();
    store.initialize(10);
    const failedAssistant = await store.sendMessage("旧的普通问题", true);
    expect(failedAssistant.retryable).toBe(true);

    store.prepareRecordAnalysis([record]);
    store.setConsentGiven(true);
    await store.retryMessage(failedAssistant.id, true);

    expect(api.streamAiChat).toHaveBeenCalledOnce();
    expect(store.preparedAnalysis).not.toBeNull();
    expect(store.selectedRecordIds).toEqual([record.id]);
    expect(store.consentGiven).toBe(true);
  });

  it("does not let an old analysis retry replace an active stream context", async () => {
    let finishStream;
    api.streamAiChat.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishStream = resolve;
        })
    );
    const store = useAiChatStore();
    store.initialize(10);
    const failedAnalysis = {
      id: "old-analysis",
      role: "assistant",
      content: "旧分析失败",
      kind: "analysis",
      failed: true,
      retryable: true,
      retryRecords: [record],
    };
    store.messages = [
      { id: "old-request", role: "user", content: "旧分析" },
      failedAnalysis,
    ];

    const pending = store.sendMessage("当前问题", true);
    const activeController = store.activeController;
    expect(store.retryAnalysis(failedAnalysis)).toBe(false);
    expect(store.preparedAnalysis).toBeNull();
    expect(store.activeController).toBe(activeController);

    finishStream();
    await pending;
  });

  it("does not offer an endless retry for a non-retryable unavailable analysis", async () => {
    const failure = Object.assign(new Error("档案不可用"), {
      code: "record_unavailable",
      retryable: false,
      status: 404,
    });
    api.streamAiAnalysis.mockRejectedValueOnce(failure);
    const store = useAiChatStore();
    store.initialize(10);
    store.prepareRecordAnalysis([record]);
    store.setConsentGiven(true);

    const assistant = await store.analyzePreparedRecords();

    expect(assistant.retryable).toBe(false);
    expect(assistant.retryRecords).toEqual([]);
    expect(store.retryAnalysis(assistant)).toBe(false);
    expect(store.recordsLoaded).toBe(false);
  });

  it("does not resend record-derived history or summaries with an unrelated chat", async () => {
    api.streamAiAnalysis.mockImplementationOnce(async (_payload, options) => {
      options.onEvent({ event: "delta", text: "含具体指标值的分析" });
      options.onEvent({
        event: "done",
        decision: "answer",
        source: "model",
        summary: "不应持久化的敏感摘要",
      });
    });
    const store = useAiChatStore();
    store.initialize(10);
    store.prepareRecordAnalysis([record]);
    store.setConsentGiven(true);
    await store.analyzePreparedRecords();

    await store.sendMessage("如何修改系统主题？", true);

    const payload = api.streamAiChat.mock.calls[0][0];
    expect(payload.history).toEqual([]);
    expect(payload.summary).toBe("");
    expect(payload.selected_record_ids).toEqual([]);
  });

  it("includes only the explicitly reauthorized sensitive result in follow-up history", async () => {
    const secondRecord = { ...record, id: 8, owner_id: 20, owner: { username: "亲友乙" } };
    api.fetchAiRecords.mockResolvedValueOnce({ data: { items: [secondRecord] } });
    const store = useAiChatStore();
    store.initialize(10);
    store.messages = [
      {
        id: "user-a",
        role: "user",
        content: "分析亲友甲",
        recordSensitive: true,
        contextRecordIds: [7],
      },
      {
        id: "assistant-a",
        role: "assistant",
        content: "亲友甲的敏感结果",
        recordSensitive: true,
        contextRecordIds: [7],
      },
      {
        id: "user-b",
        role: "user",
        content: "分析亲友乙",
        recordSensitive: true,
        contextRecordIds: [8],
      },
      {
        id: "assistant-b",
        role: "assistant",
        content: "亲友乙的敏感结果",
        recordSensitive: true,
        contextRecordIds: [8],
      },
    ];

    expect(store.prepareRecordFollowUp(store.messages[3])).toBe(true);
    await flushPromises();
    store.setConsentGiven(true);
    await store.confirmRecordPicker(true);
    await store.sendMessage("请继续解释", true);

    const payload = api.streamAiChat.mock.calls[0][0];
    expect(payload.history).toEqual([
      { role: "user", content: "分析亲友乙" },
      { role: "assistant", content: "亲友乙的敏感结果" },
    ]);
    expect(payload.history.some((item) => item.content.includes("亲友甲"))).toBe(false);
    expect(payload.selected_record_ids).toEqual([8]);
    expect(payload.consent).toBe(true);
  });

  it("restores a failed analysis as an analysis retry after a tab reload", () => {
    const firstStore = useAiChatStore();
    firstStore.initialize(10);
    firstStore.messages = [
      {
        id: "analysis-request",
        role: "user",
        content: "智能分析 1 份档案",
        kind: "analysis-request",
        recordSensitive: true,
        contextRecordIds: [record.id],
      },
      {
        id: "analysis-response",
        role: "assistant",
        content: "本次回复未完成。",
        kind: "analysis",
        failed: true,
        retryable: true,
        retryRecords: [record],
        recordSensitive: true,
        contextRecordIds: [record.id],
      },
    ];
    firstStore.persist();

    setActivePinia(createPinia());
    const restoredStore = useAiChatStore();
    restoredStore.initialize(10);
    const restored = restoredStore.messages[1];

    expect(restored.kind).toBe("analysis");
    expect(restored.retryRecords).toHaveLength(1);
    expect(restoredStore.retryAnalysis(restored)).toBe(true);
    expect(restoredStore.consentGiven).toBe(false);
  });

  it("marks a persisted partial stream as interrupted and excludes it from history", async () => {
    const firstStore = useAiChatStore();
    firstStore.initialize(10);
    firstStore.messages = [
      { id: "partial-user", role: "user", content: "未完成问题", kind: "chat" },
      {
        id: "partial-assistant",
        role: "assistant",
        content: "只收到一半",
        kind: "chat",
        streaming: true,
      },
    ];
    firstStore.persist();

    setActivePinia(createPinia());
    const restoredStore = useAiChatStore();
    restoredStore.initialize(10);
    expect(restoredStore.messages[1]).toMatchObject({
      failed: true,
      retryable: true,
      streaming: false,
      errorCode: "PAGE_RELOADED",
    });

    await restoredStore.sendMessage("新的问题", true);
    expect(api.streamAiChat.mock.calls[0][0].history).toEqual([]);
  });

  it("clears pre-v2 messages because their record sensitivity cannot be proven", async () => {
    sessionStorage.setItem(
      `${AI_SESSION_PREFIX}user-10`,
      JSON.stringify({
        messages: [
          { id: "old-user", role: "user", content: "分析我的档案" },
          { id: "old-ai", role: "assistant", content: "旧版本中的具体健康指标" },
        ],
        summary: "旧版本敏感摘要",
        isOpen: true,
      })
    );
    const store = useAiChatStore();
    store.initialize(10);

    expect(store.messages).toEqual([]);
    expect(store.summary).toBe("");
    await store.sendMessage("如何修改系统主题？", true);
    expect(api.streamAiChat.mock.calls[0][0]).toMatchObject({
      history: [],
      summary: "",
      selected_record_ids: [],
    });
  });

  it("marks cancellation without removing the optimistic user message", async () => {
    api.streamAiChat.mockImplementation(
      (_payload, { signal }) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError"))
          );
        })
    );
    const store = useAiChatStore();
    store.initialize(10);

    const pending = store.sendMessage("不要丢失这条消息", true);
    expect(store.messages[0].content).toBe("不要丢失这条消息");
    store.cancelActive();
    const assistant = await pending;

    expect(store.messages).toHaveLength(2);
    expect(assistant.cancelled).toBe(true);
    expect(assistant.retryable).toBe(true);
  });

  it("clips a long analysis before sending it as follow-up history", async () => {
    const store = useAiChatStore();
    store.initialize(10);
    store.messages = [
      { id: "request", role: "user", content: "分析档案", kind: "analysis-request" },
      { id: "analysis", role: "assistant", content: `开头${"指标说明".repeat(1200)}结尾`, kind: "analysis" },
    ];

    await store.sendMessage("请继续解释", true);

    const history = api.streamAiChat.mock.calls[0][0].history;
    expect(history).toHaveLength(2);
    expect(Array.from(history[1].content).length).toBeLessThanOrEqual(4000);
    expect(history[1].content).toContain("较早内容已在本地裁剪");
    expect(history[1].content.startsWith("开头")).toBe(true);
    expect(history[1].content.endsWith("结尾")).toBe(true);
  });
});
