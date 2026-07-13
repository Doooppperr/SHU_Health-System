import http from "./http";
import { useAuthStore } from "../stores/auth";


const AI_API_PREFIX = "/api/ai";
const DEFAULT_STREAM_TIMEOUTS = Object.freeze({
  connect: 10000,
  idle: 35000,
  total: 80000,
});

export class AiStreamError extends Error {
  constructor(message, { code = "AI_STREAM_ERROR", status = 0, retryable = false } = {}) {
    super(message);
    this.name = "AiStreamError";
    this.code = code;
    this.status = status;
    this.retryable = retryable;
  }
}

export function fetchAiRecords() {
  return http.get("/ai/records");
}

// Kept for callers that still use the non-streaming compatibility endpoint.
export function sendAiChat(payload) {
  return http.post("/ai/chat", payload, {
    timeout: 75000,
  });
}

export function streamAiChat(payload, options = {}) {
  return streamAiRequest("/chat/stream", payload, options);
}

export function streamAiAnalysis(payload, options = {}) {
  return streamAiRequest("/analyze/stream", payload, options);
}

function eventFromBlock(block) {
  let eventName = "";
  const dataLines = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      eventName = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }

  if (dataLines.length === 0) {
    return null;
  }

  const rawData = dataLines.join("\n");
  if (rawData === "[DONE]") {
    return { event: "done" };
  }

  let data;
  try {
    data = JSON.parse(rawData);
  } catch {
    throw new AiStreamError("AI 返回了无法解析的数据", {
      code: "INVALID_STREAM_DATA",
      retryable: true,
    });
  }

  if (!data || typeof data !== "object" || Array.isArray(data)) {
    data = { data };
  }
  return { ...data, event: data.event || eventName || "message" };
}

async function errorFromResponse(response) {
  let details = {};
  try {
    details = await response.json();
  } catch {
    // A proxy may return a plain-text or empty error body.
  }
  const body = details.error && typeof details.error === "object" ? details.error : details;
  const message = body.message || body.detail || `AI 请求失败（${response.status}）`;
  return new AiStreamError(message, {
    code: body.code || `HTTP_${response.status}`,
    status: response.status,
    retryable: body.retryable ?? [408, 429, 502, 503, 504].includes(response.status),
  });
}

async function fetchStreamResponse(path, payload, signal, allowRefresh) {
  const authStore = useAuthStore();
  const headers = {
    Accept: "text/event-stream",
    "Content-Type": "application/json",
  };
  if (authStore.accessToken) {
    headers.Authorization = `Bearer ${authStore.accessToken}`;
  }

  try {
    const response = await fetch(`${AI_API_PREFIX}${path}`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
      credentials: "same-origin",
      signal,
    });

    if (response.status === 401 && allowRefresh && authStore.refreshToken) {
      const refreshed = await authStore.tryRefresh();
      if (refreshed) {
        return fetchStreamResponse(path, payload, signal, false);
      }
    }
    return response;
  } catch (error) {
    if (error?.name === "AbortError") {
      throw error;
    }
    throw new AiStreamError("无法连接 AI 服务，请检查网络后重试", {
      code: "NETWORK_ERROR",
      retryable: true,
    });
  }
}

function abortError() {
  return new DOMException("Aborted", "AbortError");
}

function timeoutError(code, message) {
  return new AiStreamError(message, { code, retryable: true });
}

function timeoutSettings(overrides = {}) {
  return Object.fromEntries(
    Object.entries(DEFAULT_STREAM_TIMEOUTS).map(([key, fallback]) => {
      const value = Number(overrides[key]);
      return [key, Number.isFinite(value) && value > 0 ? value : fallback];
    })
  );
}

function phaseWatchdog(deadline, maximum, phaseError) {
  const remaining = Math.max(0, deadline - Date.now());
  if (remaining <= maximum) {
    return {
      delay: remaining,
      error: timeoutError("AI_TOTAL_TIMEOUT", "AI 回复超过总时限，请重试"),
    };
  }
  return { delay: maximum, error: phaseError };
}

function guardedOperation(operation, { controller, delay, error, onTimeout }) {
  return new Promise((resolve, reject) => {
    let settled = false;
    let timer = null;
    const signal = controller.signal;

    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      if (timer !== null) clearTimeout(timer);
      signal.removeEventListener("abort", handleAbort);
      callback(value);
    };
    const handleAbort = () => {
      finish(reject, signal.reason instanceof Error ? signal.reason : abortError());
    };

    signal.addEventListener("abort", handleAbort, { once: true });
    Promise.resolve(operation).then(
      (value) => finish(resolve, value),
      (caught) => finish(reject, caught)
    );
    if (delay <= 0 && !signal.aborted) {
      try {
        onTimeout?.();
      } finally {
        controller.abort(error);
      }
      return;
    }
    timer = setTimeout(() => {
      try {
        onTimeout?.();
      } finally {
        if (!signal.aborted) controller.abort(error);
      }
    }, delay);
    if (signal.aborted) handleAbort();
  });
}

async function streamAiRequest(
  path,
  payload,
  { signal: callerSignal, onEvent, timeouts: timeoutOverrides } = {}
) {
  const timeouts = timeoutSettings(timeoutOverrides);
  const deadline = Date.now() + timeouts.total;
  const controller = new AbortController();
  const forwardCallerAbort = () => {
    if (!controller.signal.aborted) {
      controller.abort(
        callerSignal?.reason instanceof Error ? callerSignal.reason : abortError()
      );
    }
  };
  if (callerSignal) {
    callerSignal.addEventListener("abort", forwardCallerAbort, { once: true });
    if (callerSignal.aborted) forwardCallerAbort();
  }

  let response;
  try {
    const connectWatchdog = phaseWatchdog(
      deadline,
      timeouts.connect,
      timeoutError("AI_CONNECT_TIMEOUT", "连接 AI 服务超时，请重试")
    );
    response = await guardedOperation(
      fetchStreamResponse(path, payload, controller.signal, true),
      {
        controller,
        delay: connectWatchdog.delay,
        error: connectWatchdog.error,
      }
    );

    if (!response.ok) {
      const errorBodyWatchdog = phaseWatchdog(
        deadline,
        timeouts.idle,
        timeoutError("AI_IDLE_TIMEOUT", "AI 服务错误响应读取超时，请重试")
      );
      throw await guardedOperation(errorFromResponse(response), {
        controller,
        delay: errorBodyWatchdog.delay,
        error: errorBodyWatchdog.error,
      });
    }
    if (!response.body) {
      throw new AiStreamError("AI 服务未返回可读取的数据流", {
        code: "EMPTY_STREAM",
        retryable: true,
      });
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let doneReceived = false;
    let readerFinished = false;
    const cancelReader = () => {
      try {
        Promise.resolve(reader.cancel()).catch(() => {});
      } catch {
        // Some test/polyfill readers may throw synchronously while cancelling.
      }
    };
    controller.signal.addEventListener("abort", cancelReader, { once: true });
    try {
      streamLoop: while (true) {
        const readWatchdog = phaseWatchdog(
          deadline,
          timeouts.idle,
          timeoutError("AI_IDLE_TIMEOUT", "AI 长时间没有返回新内容，请重试")
        );
        const { value, done } = await guardedOperation(reader.read(), {
          controller,
          delay: readWatchdog.delay,
          error: readWatchdog.error,
          onTimeout: cancelReader,
        });
        readerFinished = done;
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() || "";
      for (const block of blocks) {
        const event = eventFromBlock(block);
        if (!event) continue;
        onEvent?.(event);
        if (event.event === "error") {
          throw new AiStreamError(event.message || "AI 暂时无法回复", {
            code: event.code || "AI_UPSTREAM_ERROR",
            status: Number(event.status) || 0,
            retryable: event.retryable === true,
          });
        }
        if (event.event === "done") {
          doneReceived = true;
          break streamLoop;
        }
      }
      if (done) break;
      }

      if (!doneReceived && buffer.trim()) {
        const event = eventFromBlock(buffer);
        if (event) {
          onEvent?.(event);
          doneReceived ||= event.event === "done";
          if (event.event === "error") {
            throw new AiStreamError(event.message || "AI 暂时无法回复", {
              code: event.code || "AI_UPSTREAM_ERROR",
              retryable: event.retryable === true,
            });
          }
        }
      }
    } finally {
      controller.signal.removeEventListener("abort", cancelReader);
      if (!readerFinished) cancelReader();
      reader.releaseLock();
    }

    if (!doneReceived) {
      throw new AiStreamError("AI 回复意外中断，请重试", {
        code: "INCOMPLETE_STREAM",
        retryable: true,
      });
    }
  } finally {
    callerSignal?.removeEventListener("abort", forwardCallerAbort);
  }
}
