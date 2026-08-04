import { defineStore } from "pinia";

import {
  clearAgentThread,
  createAgentThread,
  fetchAgentThread,
  streamAgentDecision,
  streamAgentRun,
} from "../api/agent";
import {
  agentToolProgressLabel,
  replacePendingAgentAction,
} from "../utils/agentPresentation";


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
    pendingActions: [],
    isSending: false,
    statusText: "",
    lastError: "",
    controller: null,
    identityGeneration: 0,
  }),

  actions: {
    async initialize(userId) {
      return this.switchIdentity(userId);
    },

    async switchIdentity(userId) {
      const nextUserId = userId || null;
      if (this.userId === nextUserId && this.threadId) return;
      this.cancel();
      this.identityGeneration += 1;
      const generation = this.identityGeneration;
      this.userId = nextUserId;
      this.threadId = "";
      this.messages = [];
      this.pendingActions = [];
      this.isSending = false;
      this.statusText = "";
      this.lastError = "";
      this.controller = null;
      if (!nextUserId) return;

      const saved = sessionStorage.getItem(threadStorageKey(userId)) || "";
      if (saved) {
        try {
          const response = await fetchAgentThread(saved);
          if (this.identityGeneration !== generation || this.userId !== nextUserId) return;
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
          if (this.identityGeneration !== generation || this.userId !== nextUserId) return;
          sessionStorage.removeItem(threadStorageKey(nextUserId));
        }
      }
      await this.newThread(generation);
    },

    async newThread(expectedGeneration = this.identityGeneration) {
      const expectedUserId = this.userId;
      if (!expectedUserId) return;
      const response = await createAgentThread();
      if (
        this.identityGeneration !== expectedGeneration ||
        this.userId !== expectedUserId
      ) return;
      this.threadId = response.data.item.id;
      this.messages = [];
      this.pendingActions = [];
      sessionStorage.setItem(threadStorageKey(this.userId), this.threadId);
    },

    async send(message) {
      const content = String(message || "").trim();
      if (!content || this.isSending) return;
      const generation = this.identityGeneration;
      const userId = this.userId;
      if (!this.threadId) await this.newThread(generation);
      if (
        !this.threadId ||
        this.identityGeneration !== generation ||
        this.userId !== userId
      ) return;
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
              if (this.identityGeneration !== generation) return;
              if (event.event === "delta") assistant.content += event.content || "";
              if (event.event === "plan") this.statusText = event.message || "正在规划任务";
              if (event.event === "tool_started") {
                this.statusText = agentToolProgressLabel(event.name);
              }
              if (event.event === "tool_completed") {
                if (!event.ok) this.statusText = "正在调整查询条件";
              }
              if (event.event === "approval_required") {
                this.pendingActions = replacePendingAgentAction(
                  this.pendingActions,
                  event
                );
              }
            },
          }
        );
      } catch (error) {
        if (this.identityGeneration !== generation) return;
        assistant.failed = true;
        this.lastError = error.message || "Agent 执行失败";
        if (!assistant.content) assistant.content = this.lastError;
      } finally {
        if (this.identityGeneration !== generation) return;
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
      const generation = this.identityGeneration;
      this.isSending = true;
      this.statusText = decision === "approve" ? "正在重新校验并执行" : "正在取消操作";
      try {
        const result = await streamAgentDecision(actionId, decision);
        if (this.identityGeneration !== generation) return;
        action.status = result.status;
        this.pendingActions = this.pendingActions.filter(
          (item) => item.action_id !== actionId
        );
        this.messages.push({
          id: messageId("assistant"),
          role: "assistant",
          content: decision === "approve"
            ? "操作已完成，请查看最新业务状态。"
            : "已取消该操作，没有执行任何业务变更。",
        });
      } catch (error) {
        if (this.identityGeneration !== generation) return;
        this.lastError = error.message || "操作没有完成";
      } finally {
        if (this.identityGeneration !== generation) return;
        this.isSending = false;
        this.statusText = "";
      }
    },

    cancel() {
      this.controller?.abort();
    },

    async clear() {
      const generation = this.identityGeneration;
      const userId = this.userId;
      this.cancel();
      if (this.threadId) await clearAgentThread(this.threadId).catch(() => {});
      if (this.identityGeneration !== generation || this.userId !== userId) return;
      sessionStorage.removeItem(threadStorageKey(userId));
      this.threadId = "";
      this.messages = [];
      this.pendingActions = [];
      await this.newThread(generation);
    },
  },
});
