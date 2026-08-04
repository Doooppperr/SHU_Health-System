import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAgentStore } from "./agent";

const mocks = vi.hoisted(() => ({
  clearAgentThread: vi.fn(),
  createAgentThread: vi.fn(),
  fetchAgentThread: vi.fn(),
  streamAgentDecision: vi.fn(),
  streamAgentRun: vi.fn(),
}));

vi.mock("../api/agent", () => mocks);

describe("Agent 当前有效账号隔离", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    setActivePinia(createPinia());
    mocks.createAgentThread.mockResolvedValue({ data: { item: { id: "new-thread" } } });
  });

  it("关联账号切换后立即丢弃原账号线程并加载目标账号线程", async () => {
    sessionStorage.setItem("healthdoc-agent-thread:1", "thread-user-1");
    sessionStorage.setItem("healthdoc-agent-thread:2", "thread-user-2");
    mocks.fetchAgentThread.mockImplementation(async (threadId) => ({
      data: {
        item: {
          id: threadId,
          messages: [{ role: "assistant", content: `消息 ${threadId}` }],
          pending_actions: [],
        },
      },
    }));
    const store = useAgentStore();

    await store.switchIdentity(1);
    expect(store.threadId).toBe("thread-user-1");
    expect(store.messages[0].content).toContain("thread-user-1");

    const switching = store.switchIdentity(2);
    expect(store.userId).toBe(2);
    expect(store.messages).toEqual([]);
    await switching;

    expect(store.threadId).toBe("thread-user-2");
    expect(store.messages[0].content).toContain("thread-user-2");
    expect(store.messages[0].content).not.toContain("thread-user-1");
  });

  it("较慢的旧账号初始化结果不能覆盖已经切换的新账号", async () => {
    sessionStorage.setItem("healthdoc-agent-thread:1", "slow-thread");
    let resolveOld;
    mocks.fetchAgentThread.mockImplementation((threadId) => {
      if (threadId === "slow-thread") {
        return new Promise((resolve) => { resolveOld = resolve; });
      }
      return Promise.resolve({
        data: { item: { id: threadId, messages: [], pending_actions: [] } },
      });
    });
    const store = useAgentStore();
    const oldInitialization = store.switchIdentity(1);
    await Promise.resolve();
    await store.switchIdentity(2);
    resolveOld({
      data: { item: { id: "slow-thread", messages: [{ role: "assistant", content: "旧账号" }], pending_actions: [] } },
    });
    await oldInitialization;

    expect(store.userId).toBe(2);
    expect(store.threadId).toBe("new-thread");
    expect(store.messages).toEqual([]);
  });

  it("创建线程期间切换账号不会把旧账号消息发送到新账号", async () => {
    let resolveOldThread;
    mocks.createAgentThread
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOldThread = resolve; }))
      .mockResolvedValueOnce({ data: { item: { id: "thread-user-2" } } });
    const store = useAgentStore();
    store.userId = 1;
    store.identityGeneration = 1;

    const oldSend = store.send("旧账号预约请求");
    await Promise.resolve();
    await store.switchIdentity(2);
    resolveOldThread({ data: { item: { id: "thread-user-1" } } });
    await oldSend;

    expect(store.userId).toBe(2);
    expect(store.threadId).toBe("thread-user-2");
    expect(mocks.streamAgentRun).not.toHaveBeenCalled();
    expect(store.messages).toEqual([]);
  });
});
