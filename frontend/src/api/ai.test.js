import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({
  accessToken: "token-old",
  refreshToken: "refresh-token",
  tryRefresh: vi.fn(),
}));

vi.mock("../stores/auth", () => ({
  useAuthStore: () => auth,
}));

import { AiStreamError, streamAiChat } from "./ai";

function streamResponse(chunks, { status = 200 } = {}) {
  const values = chunks.map((chunk) => new TextEncoder().encode(chunk));
  let index = 0;
  return {
    status,
    ok: status >= 200 && status < 300,
    body: {
      getReader: () => ({
        read: vi.fn(async () =>
          index < values.length
            ? { value: values[index++], done: false }
            : { value: undefined, done: true }
        ),
        releaseLock: vi.fn(),
      }),
    },
    json: vi.fn(async () => ({})),
  };
}

function jsonResponse(status, body) {
  return {
    status,
    ok: false,
    body: null,
    json: vi.fn(async () => body),
  };
}

beforeEach(() => {
  auth.accessToken = "token-old";
  auth.refreshToken = "refresh-token";
  auth.tryRefresh.mockReset();
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("AI SSE client", () => {
  it("parses the full meta/status/delta/action/done contract across chunks", async () => {
    const events = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        streamResponse([
          'event: meta\ndata: {"request_id":"req-1","model":"deepseek"}\n\n' +
            'event: status\ndata: {"stage":"provider","message":"正在生成"}\n\n' +
            'event: delta\ndata: {"text":"第一段"}\n',
          '\nevent: action\ndata: {"action":"select_records","message":"请选择档案"}\n\n' +
            'event: done\ndata: {"decision":"answer","source":"model","request_id":"req-1"}\n\n',
        ])
      )
    );

    await streamAiChat(
      { message: "测试", history: [], selected_record_ids: [] },
      { onEvent: (event) => events.push(event) }
    );

    expect(events.map((event) => event.event)).toEqual([
      "meta",
      "status",
      "delta",
      "action",
      "done",
    ]);
    expect(events[2].text).toBe("第一段");
    expect(events[3].action).toBe("select_records");
  });

  it("refreshes once after a 401 and retries with the new access token", async () => {
    auth.tryRefresh.mockImplementation(async () => {
      auth.accessToken = "token-new";
      return true;
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(401, { message: "expired" }))
      .mockResolvedValueOnce(
        streamResponse(['event: done\ndata: {"decision":"answer"}\n\n'])
      );
    vi.stubGlobal("fetch", fetchMock);

    await streamAiChat({ message: "测试" });

    expect(auth.tryRefresh).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer token-old");
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBe("Bearer token-new");
  });

  it("turns an SSE error event into a stable retryable error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        streamResponse([
          'event: error\ndata: {"code":"AI_TIMEOUT","message":"生成超时","retryable":true}\n\n',
        ])
      )
    );

    await expect(streamAiChat({ message: "测试" })).rejects.toMatchObject({
      name: "AiStreamError",
      code: "AI_TIMEOUT",
      message: "生成超时",
      retryable: true,
    });
    expect(AiStreamError.prototype).toBeInstanceOf(Error);
  });

  it("times out a fetch that never establishes the SSE connection", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));

    const request = streamAiChat(
      { message: "测试" },
      { timeouts: { connect: 20, idle: 50, total: 100 } }
    );
    const assertion = expect(request).rejects.toMatchObject({
      code: "AI_CONNECT_TIMEOUT",
      retryable: true,
    });
    await vi.advanceTimersByTimeAsync(21);
    await assertion;
    vi.useRealTimers();
  });

  it("times out and cancels a stream whose reader stops producing data", async () => {
    vi.useFakeTimers();
    const cancel = vi.fn(async () => {});
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        status: 200,
        ok: true,
        body: {
          getReader: () => ({
            read: vi.fn(() => new Promise(() => {})),
            cancel,
            releaseLock: vi.fn(),
          }),
        },
      }))
    );

    const request = streamAiChat(
      { message: "测试" },
      { timeouts: { connect: 20, idle: 30, total: 100 } }
    );
    const assertion = expect(request).rejects.toMatchObject({
      code: "AI_IDLE_TIMEOUT",
      retryable: true,
    });
    await vi.advanceTimersByTimeAsync(31);
    await assertion;
    expect(cancel).toHaveBeenCalled();
    vi.useRealTimers();
  });

  it("times out when a non-success response body never finishes", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        status: 503,
        ok: false,
        body: null,
        json: vi.fn(() => new Promise(() => {})),
      }))
    );

    const request = streamAiChat(
      { message: "测试" },
      { timeouts: { connect: 20, idle: 30, total: 100 } }
    );
    const assertion = expect(request).rejects.toMatchObject({
      code: "AI_IDLE_TIMEOUT",
      retryable: true,
    });
    await vi.advanceTimersByTimeAsync(31);
    await assertion;
  });
});
