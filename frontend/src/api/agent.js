import http from "./http";
import { useAuthStore } from "../stores/auth";
import { appPath } from "../utils/appBase";


const AGENT_API_PREFIX = appPath("/api/agent");

export function fetchAgentCapabilities() {
  return http.get("/agent/capabilities");
}

export function createAgentThread() {
  return http.post("/agent/threads");
}

export function fetchAgentThread(threadId) {
  return http.get(`/agent/threads/${threadId}`);
}

export function clearAgentThread(threadId) {
  return http.delete(`/agent/threads/${threadId}`);
}

export function fetchAgentAction(actionId) {
  return http.get(`/agent/actions/${actionId}`);
}

export function fetchAdminSupportHandoffs(params = {}) {
  return http.get("/agent/admin/support-handoffs", { params });
}

export function updateAdminSupportHandoff(ticketId, payload) {
  return http.patch(`/agent/admin/support-handoffs/${ticketId}`, payload);
}

function parseEvent(block) {
  let event = "message";
  const data = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!data.length) return null;
  return { event, ...JSON.parse(data.join("\n")) };
}

async function stream(path, payload, { signal, onEvent } = {}, allowRefresh = true) {
  const auth = useAuthStore();
  const headers = {
    Accept: "text/event-stream",
    "Content-Type": "application/json",
  };
  if (auth.accessToken) headers.Authorization = `Bearer ${auth.accessToken}`;
  const response = await fetch(`${AGENT_API_PREFIX}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
    credentials: "same-origin",
    signal,
  });
  if (response.status === 401 && allowRefresh && auth.refreshToken) {
    if (await auth.tryRefresh()) {
      return stream(path, payload, { signal, onEvent }, false);
    }
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(body?.error?.message || body?.message || "Agent 请求失败");
    error.code = body?.error?.code || `HTTP_${response.status}`;
    error.status = response.status;
    throw error;
  }
  if (!response.body) throw new Error("Agent 未返回数据流");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let doneReceived = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() || "";
      for (const block of blocks) {
        const event = parseEvent(block);
        if (!event) continue;
        onEvent?.(event);
        if (event.event === "error") {
          const error = new Error(event.message || "Agent 执行失败");
          error.code = event.code || "AGENT_ERROR";
          error.retryable = event.retryable === true;
          throw error;
        }
        if (event.event === "done") {
          doneReceived = true;
          return event;
        }
      }
      if (done) break;
    }
  } finally {
    reader.releaseLock();
  }
  if (!doneReceived) throw new Error("Agent 数据流意外中断");
}

export function streamAgentRun(threadId, payload, options) {
  return stream(`/threads/${threadId}/runs/stream`, payload, options);
}

export function streamAgentDecision(actionId, decision, options) {
  return stream(`/actions/${actionId}/decision/stream`, { decision }, options);
}
