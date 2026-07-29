import { defineStore } from "pinia";

import {
  clearAgentThread,
  createAgentThread,
  fetchAgentThread,
  streamAgentDecision,
  streamAgentRun,
} from "../api/agent";


function threadStorageKey(userId) {
  return `healthdoc-agent-thread:${userId || "guest"}`;
}

function messageId(prefix) {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export const useAgentStore = defineStore("agent", {
  state: () => ({
    userId: null,
    threadId: "",
    messages: [],
    activity: [],
    pendingActions: [],
    isSending: false,
    statusText: "",
    lastError: "",
    controller: null,
  }),

  actions: {
    async initialize(userId) {
      if (!userId) return;
      this.userId = userId;
      const saved = sessionStorage.getItem(threadStorageKey(userId)) || "";
      if (saved) {
        try {
          const response = await fetchAgentThread(saved);
          const item = response.data.item;
          this.threadId = item.id;
          this.messages = (item.messages || []).map((message) => ({
            ...message,
            id: message.id || messageId(message.role || "message"),
          }));
          this.pendingActions = (item.pending_actions || []).map((action) => ({
            ...action,
            action_id: action.action_id || action.id,
          }));
          return;
        } catch {
          sessionStorage.removeItem(threadStorageKey(userId));
        }
      }
      await this.newThread();
    },

    async newThread() {
      const response = await createAgentThread();
      this.threadId = response.data.item.id;
      this.messages = [];
      this.activity = [];
      this.pendingActions = [];
      sessionStorage.setItem(threadStorageKey(this.userId), this.threadId);
    },

    async send(message) {
      const content = String(message || "").trim();
      if (!content || this.isSending) return;
      if (!this.threadId) await this.newThread();
      const assistant = {
        id: messageId("assistant"),
        role: "assistant",
        content: "",
        streaming: true,
      };
      this.messages.push(
        { id: messageId("user"), role: "user", content },
        assistant
      );
      this.activity = [];
      this.isSending = true;
      this.lastError = "";
      this.statusText = "正在规划任务";
      this.controller = new AbortController();
      try {
        await streamAgentRun(
          this.threadId,
          { message: content },
          {
            signal: this.controller.signal,
            onEvent: (event) => {
              if (event.event === "delta") assistant.content += event.content || "";
              if (event.event === "plan") this.statusText = event.message || "正在规划任务";
              if (event.event === "tool_started") {
                this.statusText = `正在调用 ${event.name}`;
                this.activity.push({
                  id: event.tool_call_id,
                  type: "tool",
                  name: event.name,
                  status: "running",
                });
              }
              if (event.event === "tool_completed") {
                const item = this.activity.find((row) => row.id === event.tool_call_id);
                if (item) item.status = event.ok ? "completed" : "failed";
              }
              if (event.event === "evidence") {
                this.activity.push({
                  id: messageId("evidence"),
                  type: "evidence",
                  name: event.tool,
                  result: event.result,
                });
              }
              if (event.event === "approval_required") {
                this.pendingActions.push(event);
              }
            },
          }
        );
      } catch (error) {
        assistant.failed = true;
        this.lastError = error.message || "Agent 执行失败";
        if (!assistant.content) assistant.content = this.lastError;
      } finally {
        assistant.streaming = false;
        this.isSending = false;
        this.statusText = "";
        this.controller = null;
      }
    },

    async decide(actionId, decision) {
      if (this.isSending) return;
      const action = this.pendingActions.find((item) => item.action_id === actionId);
      if (!action) return;
      this.isSending = true;
      this.statusText = decision === "approve" ? "正在重新校验并执行" : "正在取消操作";
      try {
        const result = await streamAgentDecision(actionId, decision, {
          onEvent: (event) => {
            if (event.event === "evidence") {
              this.activity.push({
                id: messageId("receipt"),
                type: "receipt",
                name: event.tool,
                result: event.result,
              });
            }
          },
        });
        action.status = result.status;
        this.pendingActions = this.pendingActions.filter(
          (item) => item.action_id !== actionId
        );
      } catch (error) {
        this.lastError = error.message || "操作没有完成";
      } finally {
        this.isSending = false;
        this.statusText = "";
      }
    },

    cancel() {
      this.controller?.abort();
    },

    async clear() {
      this.cancel();
      if (this.threadId) await clearAgentThread(this.threadId).catch(() => {});
      sessionStorage.removeItem(threadStorageKey(this.userId));
      this.threadId = "";
      this.messages = [];
      this.activity = [];
      this.pendingActions = [];
      await this.newThread();
    },
  },
});
