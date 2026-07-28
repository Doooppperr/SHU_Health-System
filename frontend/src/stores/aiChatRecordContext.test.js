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

const latestContext = {
  owner_id: 10,
  owner_name: "林晓晨",
  anchor_record_ids: [65],
  scope_mode: "selected_records",
  indicator_codes: [],
  source: "semantic",
  display_summary: "林晓晨 · 2026-07-22 体检报告",
  updated_at: 1785070800,
};

const latestResolution = {
  source: "semantic",
  owner: { id: 10, display_name: "林晓晨" },
  scope_mode: "selected_records",
  anchor_record_ids: [65],
  record_count: 1,
  date_range: { start: "2026-07-22", end: "2026-07-22" },
  indicators: [],
  records: [
    {
      id: 65,
      exam_date: "2026-07-22",
      institution_name: "澄心健康管理中心",
    },
  ],
};

const trendContext = {
  ...latestContext,
  anchor_record_ids: [57, 61, 65],
  scope_mode: "indicator_history",
  indicator_codes: ["LDL"],
  source: "inherited",
  display_summary: "林晓晨 · LDL 趋势 · 8份报告",
  updated_at: 1785070900,
};

const trendResolution = {
  ...latestResolution,
  source: "inherited",
  scope_mode: "indicator_history",
  anchor_record_ids: [57, 61, 65],
  record_count: 8,
  date_range: { start: "2023-01-15", end: "2026-07-22" },
  indicators: ["LDL"],
};

function completeWithContext(context, resolution) {
  return async (_payload, { onEvent }) => {
    onEvent({ event: "delta", text: "分析结果" });
    onEvent({
      event: "done",
      decision: "answer",
      source: "model",
      record_resolution: resolution,
      next_active_record_context: context,
    });
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
  localStorage.clear();
  sessionStorage.clear();
  api.fetchAiRecords.mockReset();
  api.streamAiChat.mockReset();
  api.streamAiAnalysis.mockReset();
  api.fetchAiRecords.mockResolvedValue({ data: { items: [], owners: [] } });
});

describe("persistent AI record context", () => {
  it("turns a manual attachment into persistent context without blocking consent", async () => {
    api.fetchAiRecords.mockResolvedValue({
      data: {
        items: [
          {
            id: 65,
            owner_id: 10,
            owner: { display_name: "林晓晨", label: "本人" },
            exam_date: "2026-07-22",
            institution: { name: "澄心健康管理中心" },
            indicator_count: 12,
          },
        ],
        owners: [
          {
            owner_id: 10,
            owner: { display_name: "林晓晨", label: "本人" },
            record_count: 1,
            date_range: { first: "2026-07-22", latest: "2026-07-22" },
          },
        ],
      },
    });
    const store = useAiChatStore();
    store.initialize(10);
    await store.loadAvailableRecords();

    store.showRecordPicker();
    store.setSelectedRecordIds([65]);
    const confirmed = await store.confirmRecordPicker(true);

    expect(confirmed).toEqual({ selectedRecordIds: [65], ownerId: null });
    expect(store.activeRecordContext).toMatchObject({
      owner_id: 10,
      owner_name: "林晓晨",
      anchor_record_ids: [65],
      scope_mode: "selected_records",
      source: "manual",
    });
  });

  it("keeps a semantically resolved report and sends it on the next turn", async () => {
    api.streamAiChat
      .mockImplementationOnce(completeWithContext(latestContext, latestResolution))
      .mockImplementationOnce(completeWithContext(latestContext, {
        ...latestResolution,
        source: "inherited",
      }));
    const store = useAiChatStore();
    store.initialize(10);

    await store.sendMessage("分析我上一次的体检报告", true);
    expect(store.activeRecordContext).toEqual(latestContext);
    expect(store.messages[1].recordResolution).toEqual(latestResolution);
    expect(store.messages[1]).toMatchObject({
      recordSensitive: true,
      contextRecordIds: [65],
      contextOwnerId: 10,
    });

    await store.sendMessage("具体分析这个报告里的 LDL", true);
    expect(api.streamAiChat.mock.calls[1][0]).toMatchObject({
      active_record_context: latestContext,
    });
    expect(store.activeRecordContext).toEqual(latestContext);
  });

  it("expands a follow-up trend while preserving the same owner", async () => {
    api.streamAiChat
      .mockImplementationOnce(completeWithContext(latestContext, latestResolution))
      .mockImplementationOnce(completeWithContext(trendContext, trendResolution));
    const store = useAiChatStore();
    store.initialize(10);

    await store.sendMessage("分析我上一次的体检报告", true);
    await store.sendMessage("这个指标最近几年的趋势呢", true);

    expect(api.streamAiChat.mock.calls[1][0].active_record_context.owner_id).toBe(10);
    expect(store.activeRecordContext).toEqual(trendContext);
    expect(store.messages.at(-1).recordResolution).toEqual(trendResolution);
    expect(store.messages.at(-1).contextOwnerId).toBe(10);
  });

  it("restores the active context for the same session after reload", async () => {
    api.streamAiChat.mockImplementationOnce(
      completeWithContext(latestContext, latestResolution)
    );
    const firstStore = useAiChatStore();
    firstStore.initialize(10);
    await firstStore.sendMessage("分析我上一次的体检报告", true);

    setActivePinia(createPinia());
    const restoredStore = useAiChatStore();
    restoredStore.initialize(10);
    expect(restoredStore.activeRecordContext).toEqual(latestContext);
    expect(restoredStore.selectedRecordIds).toEqual([65]);
  });

  it("clears context on conversation end and never carries it across identities", async () => {
    api.streamAiChat.mockImplementationOnce(
      completeWithContext(latestContext, latestResolution)
    );
    const store = useAiChatStore();
    store.initialize(10);
    await store.sendMessage("分析我上一次的体检报告", true);
    expect(store.activeRecordContext).not.toBeNull();

    store.clearConversation();
    expect(store.activeRecordContext).toBeNull();
    expect(sessionStorage.getItem(`${AI_SESSION_PREFIX}user-10`)).toBeNull();

    store.activeRecordContext = latestContext;
    store.persist();
    store.switchIdentity(20);
    expect(store.currentIdentity).toBe("user-20");
    expect(store.activeRecordContext).toBeNull();
  });

  it("keeps the same context snapshot when retrying a failed response", async () => {
    const failure = Object.assign(new Error("网络中断"), {
      code: "provider_timeout",
      retryable: true,
    });
    api.streamAiChat
      .mockImplementationOnce(completeWithContext(latestContext, latestResolution))
      .mockRejectedValueOnce(failure)
      .mockImplementationOnce(completeWithContext(latestContext, {
        ...latestResolution,
        source: "inherited",
      }));
    const store = useAiChatStore();
    store.initialize(10);

    await store.sendMessage("分析我上一次的体检报告", true);
    await store.sendMessage("继续解释这份报告", true);
    const failed = store.messages.at(-1);
    expect(failed).toMatchObject({ failed: true, retryable: true });

    await store.retryMessage(failed.id, true);
    expect(api.streamAiChat.mock.calls[2][0]).toMatchObject({
      active_record_context: latestContext,
    });
    expect(store.activeRecordContext).toEqual(latestContext);
  });

  it("accepts legacy done metadata without corrupting the conversation", async () => {
    api.streamAiChat.mockImplementationOnce(async (_payload, { onEvent }) => {
      onEvent({ event: "delta", text: "旧接口回复" });
      onEvent({
        event: "done",
        decision: "answer",
        auto_selected_records: true,
        selected_record_ids: [65],
      });
    });
    const store = useAiChatStore();
    store.initialize(10);

    await store.sendMessage("分析我的报告", true);
    expect(store.messages.at(-1)).toMatchObject({
      content: "旧接口回复",
      streaming: false,
      recordSensitive: true,
      contextRecordIds: [65],
    });
  });
});
