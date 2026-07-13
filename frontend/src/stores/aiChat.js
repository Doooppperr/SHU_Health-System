import { markRaw } from "vue";
import { defineStore } from "pinia";

import {
  fetchAiRecords,
  streamAiAnalysis,
  streamAiChat,
} from "../api/ai";
import { AI_SESSION_PREFIX } from "../utils/aiSession";


const PANEL_WIDTH_KEY = "health-ai-panel-width";
const BALL_POSITION_KEY = "health-ai-ball-position";
const MAX_STORED_MESSAGES = 40;
const AI_SESSION_SCHEMA_VERSION = 2;
const MAX_HISTORY_CONTENT_CHARS = 4000;
const HISTORY_TRUNCATION_MARKER = "\n…（较早内容已在本地裁剪）…\n";
let messageSequence = 0;

function identityKey(userId) {
  return userId ? `user-${userId}` : "guest";
}

function sessionKey(key) {
  return `${AI_SESSION_PREFIX}${key}`;
}

function newMessageId(prefix) {
  messageSequence += 1;
  return `${prefix}-${Date.now()}-${messageSequence}`;
}

function readJson(storage, key, fallback) {
  try {
    const raw = storage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    storage.removeItem(key);
    return fallback;
  }
}

function normalizeMessages(rawMessages) {
  if (!Array.isArray(rawMessages)) return [];
  return rawMessages
    .filter(
      (message) =>
        ["user", "assistant"].includes(message?.role) &&
        typeof message.content === "string"
    )
    .map((message) => {
      const interrupted = message.role === "assistant" && message.streaming === true;
      const recordSensitive = message.recordSensitive === true;
      const contextRecordIds = Array.isArray(message.contextRecordIds)
        ? [...new Set(message.contextRecordIds.map(Number).filter(Number.isInteger))]
        : [];
      const retryRecordIds = Array.isArray(message.retryRecordIds)
        ? [...new Set(message.retryRecordIds.map(Number).filter(Number.isInteger))]
        : interrupted && recordSensitive
          ? [...contextRecordIds]
          : [];
      const retryRecords = Array.isArray(message.retryRecords)
        ? message.retryRecords
            .map(recordMetadata)
            .filter(
              (record) =>
                Number.isInteger(record.id) && Number.isInteger(record.owner_id)
            )
        : [];
      return {
        id: typeof message.id === "string" ? message.id : newMessageId(message.role),
        role: message.role,
        content: message.content,
        kind: message.kind || "chat",
        decision: message.decision || "answer",
        supportPhone: message.supportPhone || "",
        source: message.source || "model",
        streaming: false,
        failed: message.failed === true || interrupted,
        cancelled: message.cancelled === true,
        retryable: message.retryable === true || interrupted,
        errorMessage:
          message.errorMessage ||
          (interrupted ? "页面刷新导致本次生成中断，可重新授权后重试。" : ""),
        action: message.action || "",
        errorCode: message.errorCode || (interrupted ? "PAGE_RELOADED" : ""),
        recordSensitive,
        contextRecordIds,
        retryRecordIds,
        retryRecords,
      };
    })
    .slice(-MAX_STORED_MESSAGES);
}

function recordMetadata(record) {
  return {
    id: Number(record.id),
    owner_id: Number(record.owner_id),
    owner_name: record.owner_name || record.owner?.username || "档案所有者",
    owner_label: record.owner_label || "",
    exam_date: record.exam_date || "",
    institution_name:
      record.institution_name || record.institution?.name || "未填写机构",
    indicator_count: Number(record.indicator_count) || 0,
  };
}

function clipHistoryContent(content) {
  const characters = Array.from(content.trim());
  if (characters.length <= MAX_HISTORY_CONTENT_CHARS) return characters.join("");

  const marker = Array.from(HISTORY_TRUNCATION_MARKER);
  const available = MAX_HISTORY_CONTENT_CHARS - marker.length;
  const headLength = Math.ceil(available / 2);
  const tailLength = available - headLength;
  return [
    ...characters.slice(0, headLength),
    ...marker,
    ...characters.slice(-tailLength),
  ].join("");
}

function historyFrom(
  messages,
  endIndex = messages.length,
  { sensitiveAssistantId = "", selectedRecordIds = [] } = {}
) {
  const history = [];
  const candidates = messages.slice(0, endIndex);
  const allowedRecordIds = new Set(selectedRecordIds);
  for (let index = 0; index < candidates.length - 1; index += 1) {
    const userMessage = candidates[index];
    const assistantMessage = candidates[index + 1];
    if (
      userMessage?.role === "user" &&
      assistantMessage?.role === "assistant" &&
      userMessage.content &&
      assistantMessage.content &&
      !userMessage.failed &&
      !assistantMessage.failed &&
      !userMessage.cancelled &&
      !assistantMessage.cancelled
    ) {
      if (userMessage.recordSensitive || assistantMessage.recordSensitive) {
        const contextRecordIds = assistantMessage.contextRecordIds || [];
        const explicitlyAllowed =
          assistantMessage.id === sensitiveAssistantId &&
          contextRecordIds.length > 0 &&
          contextRecordIds.every((id) => allowedRecordIds.has(id));
        if (!explicitlyAllowed) {
          index += 1;
          continue;
        }
      }
      history.push(
        { role: "user", content: clipHistoryContent(userMessage.content) },
        { role: "assistant", content: clipHistoryContent(assistantMessage.content) }
      );
      index += 1;
    }
  }
  return history.slice(-20);
}

function sameSelection(left, right) {
  return left.length === right.length && left.every((id, index) => id === right[index]);
}

function eventText(event) {
  return event.delta ?? event.content ?? event.text ?? "";
}

function errorText(error) {
  if (error?.status === 503) {
    return "AI 服务尚未配置或暂时不可用，请稍后重试。";
  }
  if (error?.status === 429) {
    return "发送过于频繁，请稍后再试。";
  }
  return error?.message || "AI 暂时无法回复，请稍后再试。";
}

export const useAiChatStore = defineStore("ai-chat", {
  state: () => ({
    currentIdentity: "",
    hydrated: false,
    isOpen: false,
    panelWidth:
      Number(localStorage.getItem(PANEL_WIDTH_KEY)) ||
      Math.min(640, Math.max(360, Math.round(window.innerWidth / 3))),
    ballPosition: readJson(localStorage, BALL_POSITION_KEY, null),
    messages: [],
    summary: "",
    selectedRecordIds: [],
    consentGiven: false,
    availableRecords: [],
    recordsLoaded: false,
    recordsLoading: false,
    recordsError: "",
    recordsLoadSequence: 0,
    pickerContext: null,
    pendingSensitiveHistoryAssistantId: "",
    preparedAnalysis: null,
    isSending: false,
    statusText: "",
    activeRequestId: "",
    activeController: null,
    lastError: "",
    lastModel: "",
  }),

  actions: {
    initialize(userId = null) {
      const nextIdentity = identityKey(userId);
      if (this.hydrated && this.currentIdentity === nextIdentity) return;

      this.cancelActive();
      this.activeController = null;
      this.activeRequestId = "";
      this.isSending = false;
      this.statusText = "";
      this.currentIdentity = nextIdentity;
      this.messages = [];
      this.summary = "";
      this.resetRecordContext();
      this.resetAvailableRecords();
      this.lastError = "";
      this.lastModel = "";

      const saved = readJson(sessionStorage, sessionKey(nextIdentity), null);
      if (saved) {
        const currentSchema = saved.version === AI_SESSION_SCHEMA_VERSION;
        this.messages = currentSchema ? normalizeMessages(saved.messages) : [];
        this.summary =
          currentSchema && typeof saved.summary === "string" ? saved.summary : "";
        this.isOpen = saved.isOpen === true;
        this.lastModel = typeof saved.lastModel === "string" ? saved.lastModel : "";
      }
      this.hydrated = true;
    },

    persist() {
      if (!this.currentIdentity) return;
      sessionStorage.setItem(
        sessionKey(this.currentIdentity),
        JSON.stringify({
          version: AI_SESSION_SCHEMA_VERSION,
          messages: this.messages.slice(-MAX_STORED_MESSAGES),
          summary: this.summary,
          isOpen: this.isOpen,
          lastModel: this.lastModel,
        })
      );
    },

    switchIdentity(userId = null) {
      this.hydrated = false;
      this.initialize(userId);
    },

    setOpen(value) {
      this.isOpen = value;
      this.persist();
    },

    setPanelWidth(width) {
      this.panelWidth = Math.round(width);
      localStorage.setItem(PANEL_WIDTH_KEY, String(this.panelWidth));
    },

    setBallPosition(position) {
      this.ballPosition = position;
      localStorage.setItem(BALL_POSITION_KEY, JSON.stringify(position));
    },

    setSelectedRecordIds(ids) {
      const normalized = [...new Set(ids.map(Number).filter(Number.isInteger))];
      const source = this.preparedAnalysis?.records || this.availableRecords;
      const ownerIds = new Set(
        normalized
          .map((id) => source.find((record) => record.id === id)?.owner_id)
          .filter((ownerId) => ownerId !== undefined)
      );
      if (ownerIds.size > 1) return false;

      if (!sameSelection(this.selectedRecordIds, normalized)) {
        this.selectedRecordIds = normalized;
        this.consentGiven = false;
      }
      return true;
    },

    setConsentGiven(value) {
      this.consentGiven = value === true;
      if (this.consentGiven) this.lastError = "";
    },

    resetAvailableRecords() {
      this.recordsLoadSequence += 1;
      this.availableRecords = [];
      this.recordsLoaded = false;
      this.recordsLoading = false;
      this.recordsError = "";
    },

    resetRecordContext({ keepPicker = false } = {}) {
      this.selectedRecordIds = [];
      this.consentGiven = false;
      this.preparedAnalysis = null;
      this.pendingSensitiveHistoryAssistantId = "";
      if (!keepPicker) this.pickerContext = null;
    },

    clearConversation({ close = true } = {}) {
      this.cancelActive();
      this.activeController = null;
      this.isSending = false;
      if (this.currentIdentity) sessionStorage.removeItem(sessionKey(this.currentIdentity));
      this.messages = [];
      this.summary = "";
      this.resetRecordContext();
      this.resetAvailableRecords();
      this.statusText = "";
      this.activeRequestId = "";
      this.lastError = "";
      this.lastModel = "";
      if (close) this.isOpen = false;
    },

    async loadAvailableRecords({ force = false } = {}) {
      if (this.recordsLoading || (this.recordsLoaded && !force)) return;
      const loadIdentity = this.currentIdentity;
      const loadSequence = this.recordsLoadSequence + 1;
      this.recordsLoadSequence = loadSequence;
      this.recordsLoading = true;
      this.recordsError = "";
      try {
        const { data } = await fetchAiRecords();
        if (
          this.currentIdentity !== loadIdentity ||
          this.recordsLoadSequence !== loadSequence
        ) {
          return;
        }
        this.availableRecords = (data.records || data.items || []).map(recordMetadata);
        this.recordsLoaded = true;
        const validIds = new Set(this.availableRecords.map((record) => record.id));
        this.setSelectedRecordIds(this.selectedRecordIds.filter((id) => validIds.has(id)));
      } catch (error) {
        if (
          this.currentIdentity !== loadIdentity ||
          this.recordsLoadSequence !== loadSequence
        ) {
          return;
        }
        this.recordsError = error?.response?.data?.message || "档案列表加载失败";
      } finally {
        if (
          this.currentIdentity === loadIdentity &&
          this.recordsLoadSequence === loadSequence
        ) {
          this.recordsLoading = false;
        }
      }
    },

    showRecordPicker({
      assistantId = null,
      query = "",
      mode = "manual",
      preselectedIds = [],
      historyAssistantId = "",
    } = {}) {
      if (this.isSending && mode !== "action") return false;
      this.preparedAnalysis = null;
      this.pendingSensitiveHistoryAssistantId = "";
      this.selectedRecordIds = [
        ...new Set(preselectedIds.map(Number).filter(Number.isInteger)),
      ];
      this.consentGiven = false;
      this.pickerContext = { assistantId, query, mode, historyAssistantId };
      void this.loadAvailableRecords({ force: true });
      return true;
    },

    closeRecordPicker() {
      this.resetRecordContext();
    },

    async confirmRecordPicker(authenticated) {
      if (!this.pickerContext || this.selectedRecordIds.length === 0) return null;
      if (!this.consentGiven) {
        this.lastError = "请先确认所选指标将发送至 DeepSeek API 处理。";
        return null;
      }

      const context = { ...this.pickerContext };
      this.pickerContext = null;
      this.lastError = "";
      if (context.mode === "action" && context.assistantId) {
        return this.retryMessage(context.assistantId, authenticated, {
          selectedRecordIds: [...this.selectedRecordIds],
          consent: true,
          sensitiveHistoryAssistantId: context.historyAssistantId || "",
        });
      }

      // Manual references are intentionally kept for exactly the next message.
      this.pendingSensitiveHistoryAssistantId = context.historyAssistantId || "";
      return { selectedRecordIds: [...this.selectedRecordIds] };
    },

    prepareRecordAnalysis(records) {
      if (this.isSending) return false;
      const normalized = (records || [])
        .map(recordMetadata)
        .filter((record) => Number.isInteger(record.id) && Number.isInteger(record.owner_id));
      if (normalized.length === 0) return false;
      if (new Set(normalized.map((record) => record.owner_id)).size !== 1) return false;

      const dates = normalized.map((record) => record.exam_date).filter(Boolean).sort();
      this.pickerContext = null;
      this.preparedAnalysis = {
        records: normalized,
        ownerId: normalized[0].owner_id,
        ownerName: normalized[0].owner_name,
        dateRange: dates.length ? `${dates[0]} 至 ${dates.at(-1)}` : "日期未填写",
      };
      this.selectedRecordIds = normalized.map((record) => record.id);
      this.consentGiven = false;
      this.lastError = "";
      this.isOpen = true;
      this.persist();
      return true;
    },

    async sendMessage(content, authenticated) {
      const message = content.trim();
      if (!message || this.isSending || this.pickerContext || this.preparedAnalysis) {
        return null;
      }

      const selectedRecordIds = authenticated ? [...this.selectedRecordIds] : [];
      const sensitiveHistoryAssistantId = this.pendingSensitiveHistoryAssistantId;
      if (selectedRecordIds.length && !this.consentGiven) {
        this.lastError = "请先确认所选指标将发送至 DeepSeek API 处理。";
        return null;
      }

      const userMessage = {
        id: newMessageId("user"),
        role: "user",
        content: message,
        kind: "chat",
        recordSensitive: selectedRecordIds.length > 0,
        contextRecordIds: [...selectedRecordIds],
      };
      const assistantMessage = {
        id: newMessageId("assistant"),
        role: "assistant",
        content: "",
        kind: "chat",
        streaming: true,
        decision: "answer",
        supportPhone: "",
        source: "model",
        recordSensitive: selectedRecordIds.length > 0,
        contextRecordIds: [...selectedRecordIds],
        retryRecordIds: [],
      };
      const insertionIndex = this.messages.length;
      this.messages.push(userMessage, assistantMessage);
      const reactiveUserMessage = this.messages[insertionIndex];
      const reactiveAssistantMessage = this.messages[insertionIndex + 1];
      this.pickerContext = null;
      await this.runStream({
        assistantMessage: reactiveAssistantMessage,
        userMessage: reactiveUserMessage,
        stream: streamAiChat,
        payload: {
          message,
          history: historyFrom(this.messages.slice(0, insertionIndex), undefined, {
            sensitiveAssistantId: sensitiveHistoryAssistantId,
            selectedRecordIds,
          }),
          summary: this.summary,
          selected_record_ids: selectedRecordIds,
          consent: selectedRecordIds.length > 0 && this.consentGiven,
        },
      });
      return reactiveAssistantMessage;
    },

    async retryMessage(assistantId, authenticated, requestContext = null) {
      if (this.isSending) return null;
      const assistantIndex = this.messages.findIndex((message) => message.id === assistantId);
      const userMessage = this.messages[assistantIndex - 1];
      const assistantMessage = this.messages[assistantIndex];
      if (assistantIndex < 1 || userMessage?.role !== "user" || assistantMessage?.role !== "assistant") {
        return null;
      }
      if (assistantMessage.failed && !assistantMessage.retryable) {
        return null;
      }

      if (
        !requestContext &&
        (this.pickerContext ||
          this.preparedAnalysis ||
          this.selectedRecordIds.length > 0 ||
          this.consentGiven ||
          this.pendingSensitiveHistoryAssistantId)
      ) {
        return null;
      }

      const requiredRecordIds = assistantMessage.retryRecordIds || [];
      if (requiredRecordIds.length > 0 && !requestContext) {
        this.showRecordPicker({
          assistantId: assistantMessage.id,
          query: userMessage.content,
          mode: "action",
          preselectedIds: requiredRecordIds,
        });
        return null;
      }

      const selectedRecordIds =
        authenticated && requestContext?.consent === true
          ? [
              ...new Set(
                (requestContext.selectedRecordIds || [])
                  .map(Number)
                  .filter(Number.isInteger)
              ),
            ]
          : [];
      const sensitiveHistoryAssistantId =
        requestContext?.sensitiveHistoryAssistantId || "";
      if (requestContext && selectedRecordIds.length === 0) {
        this.lastError = "请先确认所选指标将发送至 DeepSeek API 处理。";
        return null;
      }
      Object.assign(assistantMessage, {
        content: "",
        streaming: true,
        failed: false,
        cancelled: false,
        retryable: false,
        errorMessage: "",
        action: "",
        errorCode: "",
        recordSensitive: selectedRecordIds.length > 0,
        contextRecordIds: [...selectedRecordIds],
        retryRecordIds: [],
      });
      userMessage.recordSensitive = selectedRecordIds.length > 0;
      userMessage.contextRecordIds = [...selectedRecordIds];
      this.pickerContext = null;
      await this.runStream({
        assistantMessage,
        userMessage,
        stream: streamAiChat,
        payload: {
          message: userMessage.content,
          history: historyFrom(this.messages, assistantIndex - 1, {
            sensitiveAssistantId: sensitiveHistoryAssistantId,
            selectedRecordIds,
          }),
          summary: this.summary,
          selected_record_ids: selectedRecordIds,
          consent: selectedRecordIds.length > 0 && this.consentGiven,
        },
      });
      return assistantMessage;
    },

    async analyzePreparedRecords() {
      if (!this.preparedAnalysis || this.isSending) return null;
      if (!this.consentGiven) {
        this.lastError = "请先确认所选指标将发送至 DeepSeek API 处理。";
        return null;
      }

      const analysis = this.preparedAnalysis;
      const ids = [...this.selectedRecordIds];
      const userMessage = {
        id: newMessageId("user-analysis"),
        role: "user",
        content: `智能分析 ${ids.length} 份档案（${analysis.dateRange}）`,
        kind: "analysis-request",
        recordSensitive: true,
        contextRecordIds: [...ids],
      };
      const assistantMessage = {
        id: newMessageId("assistant-analysis"),
        role: "assistant",
        content: "",
        kind: "analysis",
        streaming: true,
        decision: "answer",
        supportPhone: "",
        source: "model",
        retryRecords: analysis.records,
        recordSensitive: true,
        contextRecordIds: [...ids],
        retryRecordIds: [],
      };
      const insertionIndex = this.messages.length;
      this.messages.push(userMessage, assistantMessage);
      const reactiveUserMessage = this.messages[insertionIndex];
      const reactiveAssistantMessage = this.messages[insertionIndex + 1];
      await this.runStream({
        assistantMessage: reactiveAssistantMessage,
        userMessage: reactiveUserMessage,
        stream: streamAiAnalysis,
        payload: { selected_record_ids: ids, consent: true },
      });
      return reactiveAssistantMessage;
    },

    retryAnalysis(message) {
      if (
        this.isSending ||
        message?.retryable !== true ||
        !Array.isArray(message?.retryRecords) ||
        message.retryRecords.length === 0
      ) {
        return false;
      }
      return this.prepareRecordAnalysis(message.retryRecords);
    },

    prepareRecordFollowUp(message) {
      if (
        this.isSending ||
        !Array.isArray(message?.contextRecordIds) ||
        message.contextRecordIds.length === 0
      ) {
        return false;
      }
      return this.showRecordPicker({
        mode: "manual",
        preselectedIds: message.contextRecordIds,
        historyAssistantId: message.id,
      });
    },

    async runStream({ assistantMessage, userMessage, stream, payload }) {
      const controller = markRaw(new AbortController());
      const requestIdentity = this.currentIdentity;
      this.activeController = controller;
      this.isSending = true;
      this.statusText = "正在连接 AI…";
      this.lastError = "";
      let actionRequested = false;

      try {
        await stream(payload, {
          signal: controller.signal,
          onEvent: (event) => {
            if (
              this.activeController !== controller ||
              this.currentIdentity !== requestIdentity
            ) {
              return;
            }
            if (event.event === "meta") {
              this.activeRequestId = event.request_id || this.activeRequestId;
              this.lastModel = event.model || this.lastModel;
            } else if (event.event === "status") {
              this.statusText = event.message || event.status || "正在生成回复…";
            } else if (event.event === "delta") {
              assistantMessage.content += String(eventText(event));
              this.statusText = "正在生成回复…";
            } else if (event.event === "action") {
              const action = event.action || event.type;
              if (action === "select_records") {
                actionRequested = true;
                assistantMessage.action = "select_records";
                if (!assistantMessage.content) {
                  assistantMessage.content = event.message || "需要参考个人档案才能继续，请选择本次要引用的档案。";
                }
                this.showRecordPicker({
                  assistantId: assistantMessage.id,
                  query: userMessage.content,
                  mode: "action",
                });
              }
            } else if (event.event === "done") {
              if (!assistantMessage.content && (event.reply || event.content)) {
                assistantMessage.content = event.reply || event.content;
              }
              assistantMessage.decision = event.decision || assistantMessage.decision;
              assistantMessage.supportPhone = event.support_phone || "";
              assistantMessage.source = event.source || assistantMessage.source;
              if (!assistantMessage.recordSensitive) {
                this.summary = event.summary || this.summary;
              }
              this.lastModel = event.model || this.lastModel;
            }
          },
        });
        if (!assistantMessage.content && !actionRequested) {
          assistantMessage.content = "AI 已处理请求，但没有返回可显示的内容。请换一种方式提问。";
        }
        assistantMessage.streaming = false;
        assistantMessage.retryRecordIds = [];
      } catch (error) {
        const cancelled = error?.name === "AbortError";
        const retryable = cancelled || error?.retryable === true;
        assistantMessage.streaming = false;
        assistantMessage.failed = true;
        assistantMessage.cancelled = cancelled;
        assistantMessage.retryable = retryable;
        assistantMessage.errorCode = error?.code || (cancelled ? "CANCELLED" : "");
        assistantMessage.errorMessage = cancelled ? "已取消本次生成" : errorText(error);
        if (assistantMessage.kind === "analysis" && !retryable) {
          assistantMessage.retryRecords = [];
          this.resetAvailableRecords();
        } else if (assistantMessage.recordSensitive) {
          assistantMessage.retryRecordIds = retryable
            ? [...(assistantMessage.contextRecordIds || [])]
            : [];
        }
        if (!assistantMessage.content) {
          assistantMessage.content = cancelled ? "本次生成已取消。" : "本次回复未完成。";
        }
        if (
          this.activeController === controller &&
          this.currentIdentity === requestIdentity
        ) {
          this.lastError = assistantMessage.errorMessage;
        }
      } finally {
        if (
          this.activeController === controller &&
          this.currentIdentity === requestIdentity
        ) {
          this.isSending = false;
          this.statusText = "";
          this.activeRequestId = "";
          this.activeController = null;
          this.resetRecordContext({ keepPicker: actionRequested });
          this.persist();
        }
      }
    },

    cancelActive() {
      this.activeController?.abort?.();
    },
  },
});
