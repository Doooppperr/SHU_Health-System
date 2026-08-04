import { markRaw } from "vue";
import { defineStore } from "pinia";

import {
  fetchAiRecords,
  streamAiAnalysis,
  streamAiChat,
} from "../api/ai";
import { AI_SESSION_PREFIX } from "../utils/aiSession";
import { redactHealthIdentityCodes } from "../utils/sensitiveData";


const PANEL_WIDTH_KEY = "health-ai-panel-width";
const MAX_STORED_MESSAGES = 40;
const AI_SESSION_SCHEMA_VERSION = 4;
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
    return raw ? redactHealthIdentityCodes(JSON.parse(raw)) : fallback;
  } catch {
    storage.removeItem(key);
    return fallback;
  }
}

function positiveIntegerOrNull(value) {
  if (value === null || value === undefined || typeof value === "boolean") return null;
  const normalized = Number(value);
  return Number.isInteger(normalized) && normalized > 0 ? normalized : null;
}

function normalizeMessages(rawMessages) {
  const safeMessages = redactHealthIdentityCodes(rawMessages);
  if (!Array.isArray(safeMessages)) return [];
  return safeMessages
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
      const contextOwnerId = positiveIntegerOrNull(message.contextOwnerId);
      const persistedRetryOwnerId = positiveIntegerOrNull(message.retryOwnerId);
      const retryOwnerId = persistedRetryOwnerId !== null
        ? persistedRetryOwnerId
        : interrupted && recordSensitive
          ? contextOwnerId
          : null;
      return {
        id: typeof message.id === "string" ? message.id : newMessageId(message.role),
        role: message.role,
        content: message.content,
        kind: message.kind || "chat",
        decision: message.decision || "answer",
        source: message.source || "model",
        streaming: false,
        failed: message.failed === true || interrupted,
        cancelled: message.cancelled === true,
        retryable: message.retryable === true || interrupted,
        errorMessage:
          message.errorMessage ||
          (interrupted ? "页面刷新导致本次生成中断，可直接重试。" : ""),
        action: message.action || "",
        errorCode: message.errorCode || (interrupted ? "PAGE_RELOADED" : ""),
        recordSensitive,
        contextRecordIds,
        retryRecordIds,
        retryRecords,
        contextOwnerId,
        retryOwnerId,
        contextSources: Array.isArray(message.contextSources) ? message.contextSources : [],
        recordResolution: message.recordResolution || null,
        requestRecordContext: normalizeActiveRecordContext(message.requestRecordContext),
      };
    })
    .slice(-MAX_STORED_MESSAGES);
}

function recordMetadata(record) {
  const safeRecord = redactHealthIdentityCodes(record || {});
  return {
    id: Number(safeRecord.id),
    owner_id: Number(safeRecord.owner_id),
    owner_name:
      safeRecord.owner_name || safeRecord.owner?.display_name || "档案所有者",
    owner_label: safeRecord.owner_label || safeRecord.owner?.label || "",
    exam_date: safeRecord.exam_date || "",
    institution_name:
      safeRecord.institution_name || safeRecord.institution?.name || "未填写机构",
    indicator_count: Number(safeRecord.indicator_count) || 0,
  };
}

function normalizeActiveRecordContext(raw) {
  const safeRaw = redactHealthIdentityCodes(raw);
  if (!safeRaw || typeof safeRaw !== "object") return null;
  const ownerId = positiveIntegerOrNull(safeRaw.owner_id);
  const scopeMode = ["selected_records", "all_confirmed", "indicator_history"].includes(
    safeRaw.scope_mode
  )
    ? safeRaw.scope_mode
    : "selected_records";
  const anchorRecordIds = Array.isArray(safeRaw.anchor_record_ids)
    ? [...new Set(safeRaw.anchor_record_ids.map(Number).filter(Number.isInteger))]
    : [];
  if (ownerId === null || (scopeMode === "selected_records" && !anchorRecordIds.length)) {
    return null;
  }
  return {
    owner_id: ownerId,
    owner_name: String(safeRaw.owner_name || "档案所有者"),
    anchor_record_ids: anchorRecordIds,
    scope_mode: scopeMode,
    indicator_codes: Array.isArray(safeRaw.indicator_codes)
      ? [...new Set(safeRaw.indicator_codes.map(String).filter(Boolean))]
      : [],
    source: safeRaw.source || "manual",
    display_summary: String(safeRaw.display_summary || ""),
    updated_at: Number(safeRaw.updated_at) || Date.now(),
  };
}

function clipHistoryContent(content) {
  const characters = Array.from(
    redactHealthIdentityCodes(String(content || "").trim())
  );
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
  { sensitiveAssistantId = "", selectedRecordIds = [], selectedOwnerId = null } = {}
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
        const sameOwner =
          Number.isInteger(selectedOwnerId) &&
          Number(assistantMessage.contextOwnerId) === selectedOwnerId;
        if (!explicitlyAllowed && !sameOwner) {
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
  return redactHealthIdentityCodes(
    error?.message || "AI 暂时无法回复，请稍后再试。"
  );
}

function messageMentionsRecords(message) {
  return /(档案|报告|体检|检查|指标|趋势|变化|异常|参考范围|肝功能|肾功能|血脂|血糖|血压|继续|这个|这份|其中|刚才)/u.test(
    String(message || "")
  );
}

export const useAiChatStore = defineStore("ai-chat", {
  state: () => ({
    currentIdentity: "",
    hydrated: false,
    isOpen: false,
    panelWidth:
      Number(localStorage.getItem(PANEL_WIDTH_KEY)) ||
      Math.min(640, Math.max(360, Math.round(window.innerWidth / 3))),
    messages: [],
    summary: "",
    activeRecordContext: null,
    selectedRecordIds: [],
    autoSelectRecords: false,
    availableRecords: [],
    availableOwners: [],
    recordSelectionMode: "records",
    selectedOwnerId: null,
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
      this.resetRecordContext({ persistState: false });
      this.resetAvailableRecords();
      this.lastError = "";
      this.lastModel = "";
      this.autoSelectRecords = false;

      const saved = readJson(sessionStorage, sessionKey(nextIdentity), null);
      if (saved) {
        const currentSchema = saved.version === AI_SESSION_SCHEMA_VERSION;
        this.messages = currentSchema ? normalizeMessages(saved.messages) : [];
        this.summary =
          currentSchema && typeof saved.summary === "string"
            ? redactHealthIdentityCodes(saved.summary)
            : "";
        this.isOpen = saved.isOpen === true;
        this.lastModel = typeof saved.lastModel === "string" ? saved.lastModel : "";
        this.activeRecordContext = currentSchema
          ? normalizeActiveRecordContext(saved.activeRecordContext)
          : null;
        this.applyActiveContextSelection();
      }
      this.hydrated = true;
      // Rewrite an existing session immediately so data saved by an older
      // frontend cannot leave a raw health identity code in browser storage.
      if (saved) this.persist();
    },

    persist() {
      if (!this.currentIdentity) return;
      sessionStorage.setItem(
        sessionKey(this.currentIdentity),
        JSON.stringify(
          redactHealthIdentityCodes({
            version: AI_SESSION_SCHEMA_VERSION,
            messages: this.messages.slice(-MAX_STORED_MESSAGES),
            summary: this.summary,
            isOpen: this.isOpen,
            lastModel: this.lastModel,
            activeRecordContext: this.activeRecordContext,
          })
        )
      );
    },

    switchIdentity(userId = null) {
      const nextIdentity = identityKey(userId);
      if (this.currentIdentity && this.currentIdentity !== nextIdentity) {
        sessionStorage.removeItem(sessionKey(this.currentIdentity));
        sessionStorage.removeItem(sessionKey(nextIdentity));
      }
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
        if (normalized.length) this.selectedOwnerId = null;
      }
      return true;
    },

    setRecordSelectionMode(mode) {
      const normalized = mode === "owner" ? "owner" : "records";
      if (this.recordSelectionMode === normalized) return;
      this.recordSelectionMode = normalized;
      this.selectedRecordIds = [];
      this.selectedOwnerId = null;
    },

    setSelectedOwnerId(ownerId) {
      this.selectedOwnerId = positiveIntegerOrNull(ownerId);
      if (this.selectedOwnerId !== null) this.selectedRecordIds = [];
    },

    setAutoSelectRecords(value) {
      this.autoSelectRecords = value === true;
    },

    resetAvailableRecords() {
      this.recordsLoadSequence += 1;
      this.availableRecords = [];
      this.availableOwners = [];
      this.recordsLoaded = false;
      this.recordsLoading = false;
      this.recordsError = "";
    },

    applyActiveContextSelection() {
      const context = this.activeRecordContext;
      if (!context) {
        this.selectedRecordIds = [];
        this.selectedOwnerId = null;
        this.recordSelectionMode = "records";
        return;
      }
      if (context.scope_mode === "all_confirmed") {
        this.recordSelectionMode = "owner";
        this.selectedOwnerId = context.owner_id;
        this.selectedRecordIds = [];
      } else {
        this.recordSelectionMode = "records";
        this.selectedOwnerId = null;
        this.selectedRecordIds = [...context.anchor_record_ids];
      }
    },

    resetRecordContext({
      keepPicker = false,
      clearActive = true,
      persistState = true,
    } = {}) {
      this.selectedRecordIds = [];
      this.selectedOwnerId = null;
      this.recordSelectionMode = "records";
      this.preparedAnalysis = null;
      this.pendingSensitiveHistoryAssistantId = "";
      if (!keepPicker) this.pickerContext = null;
      if (clearActive) this.activeRecordContext = null;
      if (persistState) this.persist();
    },

    clearConversation({ close = true } = {}) {
      this.cancelActive();
      this.activeController = null;
      this.isSending = false;
      if (this.currentIdentity) sessionStorage.removeItem(sessionKey(this.currentIdentity));
      this.messages = [];
      this.summary = "";
      this.resetRecordContext({ persistState: false });
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
        const response = await fetchAiRecords();
        const data = redactHealthIdentityCodes(response.data || {});
        if (
          this.currentIdentity !== loadIdentity ||
          this.recordsLoadSequence !== loadSequence
        ) {
          return;
        }
        this.availableRecords = (data.records || data.items || []).map(recordMetadata);
        this.availableOwners = Array.isArray(data.owners)
          ? data.owners.map((item) => ({
              owner_id: Number(item.owner_id),
              owner_name: item.owner?.display_name || "档案所有者",
              owner_label: item.owner?.label || "",
              record_count: Number(item.record_count) || 0,
              date_range: item.date_range || {},
            }))
          : [...new Set(this.availableRecords.map((item) => item.owner_id))].map(
              (ownerId) => {
                const records = this.availableRecords.filter(
                  (item) => item.owner_id === ownerId
                );
                const dates = records.map((item) => item.exam_date).filter(Boolean).sort();
                return {
                  owner_id: ownerId,
                  owner_name: records[0]?.owner_name || "档案所有者",
                  owner_label: records[0]?.owner_label || "",
                  record_count: records.length,
                  date_range: { first: dates[0] || "", latest: dates.at(-1) || "" },
                };
              }
            );
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
      preselectedOwnerId = null,
      historyAssistantId = "",
    } = {}) {
      if (this.isSending && mode !== "action") return false;
      const normalizedPreselectedOwnerId = Number(preselectedOwnerId);
      const hasPreselectedOwner =
        preselectedOwnerId !== null &&
        preselectedOwnerId !== undefined &&
        Number.isInteger(normalizedPreselectedOwnerId) &&
        normalizedPreselectedOwnerId > 0;
      this.preparedAnalysis = null;
      this.pendingSensitiveHistoryAssistantId = "";
      const active = this.activeRecordContext;
      const effectiveIds = preselectedIds.length
        ? preselectedIds
        : active?.scope_mode !== "all_confirmed"
          ? active?.anchor_record_ids || []
          : [];
      const effectiveOwnerId = hasPreselectedOwner
        ? normalizedPreselectedOwnerId
        : active?.scope_mode === "all_confirmed"
          ? active.owner_id
          : null;
      this.selectedRecordIds = [
        ...new Set(effectiveIds.map(Number).filter(Number.isInteger)),
      ];
      this.recordSelectionMode = Number.isInteger(effectiveOwnerId)
        ? "owner"
        : "records";
      this.selectedOwnerId = Number.isInteger(effectiveOwnerId)
        ? effectiveOwnerId
        : null;
      this.pickerContext = { assistantId, query, mode, historyAssistantId };
      void this.loadAvailableRecords({ force: true });
      return true;
    },

    closeRecordPicker() {
      this.pickerContext = null;
      this.applyActiveContextSelection();
      this.lastError = "";
    },

    async confirmRecordPicker(authenticated) {
      const ownerScope =
        this.recordSelectionMode === "owner" && Number.isInteger(this.selectedOwnerId);
      if (!this.pickerContext || (!ownerScope && this.selectedRecordIds.length === 0)) return null;

      const context = { ...this.pickerContext };
      this.pickerContext = null;
      this.lastError = "";
      const selectedRecords = this.availableRecords.filter((record) =>
        this.selectedRecordIds.includes(record.id)
      );
      const owner = ownerScope
        ? this.availableOwners.find((item) => item.owner_id === this.selectedOwnerId)
        : this.availableOwners.find(
            (item) => item.owner_id === selectedRecords[0]?.owner_id
          );
      const dates = selectedRecords.map((record) => record.exam_date).filter(Boolean).sort();
      this.activeRecordContext = normalizeActiveRecordContext({
        owner_id: ownerScope ? this.selectedOwnerId : selectedRecords[0]?.owner_id,
        owner_name: owner?.owner_name || selectedRecords[0]?.owner_name,
        anchor_record_ids: ownerScope ? [] : [...this.selectedRecordIds],
        scope_mode: ownerScope ? "all_confirmed" : "selected_records",
        indicator_codes: [],
        source: "manual",
        display_summary: ownerScope
          ? `${owner?.owner_name || "档案所有者"} · 全部历史 · ${owner?.record_count || 0}份报告`
          : `${selectedRecords[0]?.owner_name || "档案所有者"} · ${
              dates.length === 1 ? `${dates[0]} 体检报告` : `${selectedRecords.length}份体检报告`
            }`,
        updated_at: Date.now(),
      });
      this.applyActiveContextSelection();
      this.persist();
      if (context.mode === "action" && context.assistantId) {
        return this.retryMessage(context.assistantId, authenticated);
      }

      this.pendingSensitiveHistoryAssistantId = context.historyAssistantId || "";
      return {
        selectedRecordIds: [...this.selectedRecordIds],
        ownerId: ownerScope ? this.selectedOwnerId : null,
      };
    },

    setActiveRecordContext(context) {
      const normalized = normalizeActiveRecordContext(context);
      if (!normalized) return false;
      this.activeRecordContext = normalized;
      this.applyActiveContextSelection();
      this.persist();
      return true;
    },

    clearActiveRecordContext() {
      this.resetRecordContext({ clearActive: true });
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
      this.lastError = "";
      this.isOpen = true;
      this.persist();
      return true;
    },

    async sendMessage(content, authenticated) {
      const message = redactHealthIdentityCodes(String(content || "").trim());
      if (!message || this.isSending || this.pickerContext || this.preparedAnalysis) {
        return null;
      }

      const requestRecordContext = authenticated
        ? normalizeActiveRecordContext(this.activeRecordContext)
        : null;
      const selectedRecordIds = requestRecordContext?.anchor_record_ids || [];
      const selectedOwnerId = requestRecordContext?.owner_id || null;
      const hasRecordContext = Boolean(requestRecordContext);
      const includeLegacyContext =
        hasRecordContext && messageMentionsRecords(message);
      const sensitiveHistoryAssistantId = this.pendingSensitiveHistoryAssistantId;

      const userMessage = {
        id: newMessageId("user"),
        role: "user",
        content: message,
        kind: "chat",
        recordSensitive: hasRecordContext,
        contextRecordIds: [...selectedRecordIds],
        contextOwnerId: selectedOwnerId,
      };
      const assistantMessage = {
        id: newMessageId("assistant"),
        role: "assistant",
        content: "",
        kind: "chat",
        streaming: true,
        decision: "answer",
        source: "model",
        recordSensitive: hasRecordContext,
        contextRecordIds: [...selectedRecordIds],
        contextOwnerId: selectedOwnerId,
        retryRecordIds: [],
        retryOwnerId: null,
        requestRecordContext,
        recordResolution: null,
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
            selectedOwnerId: includeLegacyContext ? selectedOwnerId : null,
          }),
          summary: this.summary,
          active_record_context: requestRecordContext || undefined,
          selected_record_ids:
            includeLegacyContext && requestRecordContext?.scope_mode !== "all_confirmed"
              ? selectedRecordIds
              : requestRecordContext?.scope_mode === "all_confirmed"
                ? undefined
                : [],
          record_scope:
            includeLegacyContext && requestRecordContext?.scope_mode === "all_confirmed"
              ? { owner_id: selectedOwnerId, mode: "all_confirmed" }
              : undefined,
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

      if (this.pickerContext || this.preparedAnalysis) return null;
      const retryContext = authenticated
        ? normalizeActiveRecordContext(
            requestContext?.activeRecordContext ||
              assistantMessage.requestRecordContext ||
              this.activeRecordContext
          )
        : null;
      const selectedRecordIds = retryContext?.anchor_record_ids || [];
      const selectedOwnerId = retryContext?.owner_id || null;
      const includeLegacyContext =
        Boolean(retryContext) && messageMentionsRecords(userMessage.content);
      const sensitiveHistoryAssistantId =
        requestContext?.sensitiveHistoryAssistantId || "";
      userMessage.content = redactHealthIdentityCodes(userMessage.content);
      Object.assign(assistantMessage, {
        content: "",
        streaming: true,
        failed: false,
        cancelled: false,
        retryable: false,
        errorMessage: "",
        action: "",
        errorCode: "",
        recordSensitive: selectedRecordIds.length > 0 || selectedOwnerId !== null,
        contextRecordIds: [...selectedRecordIds],
        contextOwnerId: selectedOwnerId,
        retryRecordIds: [],
        retryOwnerId: null,
        requestRecordContext: retryContext,
      });
      userMessage.recordSensitive = selectedRecordIds.length > 0 || selectedOwnerId !== null;
      userMessage.contextRecordIds = [...selectedRecordIds];
      userMessage.contextOwnerId = selectedOwnerId;
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
            selectedOwnerId: includeLegacyContext ? selectedOwnerId : null,
          }),
          summary: this.summary,
          active_record_context: retryContext || undefined,
          selected_record_ids:
            includeLegacyContext && retryContext?.scope_mode !== "all_confirmed"
              ? selectedRecordIds
              : retryContext?.scope_mode === "all_confirmed"
                ? undefined
                : [],
          record_scope:
            includeLegacyContext && retryContext?.scope_mode === "all_confirmed"
              ? { owner_id: selectedOwnerId, mode: "all_confirmed" }
              : undefined,
        },
      });
      return assistantMessage;
    },

    async analyzePreparedRecords() {
      if (!this.preparedAnalysis || this.isSending) return null;

      const analysis = this.preparedAnalysis;
      const ids = [...this.selectedRecordIds];
      this.setActiveRecordContext({
        owner_id: analysis.ownerId,
        owner_name: analysis.ownerName,
        anchor_record_ids: ids,
        scope_mode: "selected_records",
        indicator_codes: [],
        source: "manual",
        display_summary: `${analysis.ownerName} · ${ids.length}份体检报告`,
        updated_at: Date.now(),
      });
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
        source: "model",
        retryRecords: analysis.records,
        recordSensitive: true,
        contextRecordIds: [...ids],
        retryRecordIds: [],
        requestRecordContext: this.activeRecordContext,
      };
      const insertionIndex = this.messages.length;
      this.messages.push(userMessage, assistantMessage);
      const reactiveUserMessage = this.messages[insertionIndex];
      const reactiveAssistantMessage = this.messages[insertionIndex + 1];
      // The prepared-analysis card is a one-time UI action; the selected
      // record context itself remains active for subsequent chat turns.
      this.preparedAnalysis = null;
      await this.runStream({
        assistantMessage: reactiveAssistantMessage,
        userMessage: reactiveUserMessage,
        stream: streamAiAnalysis,
        payload: { selected_record_ids: ids },
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
        await stream(redactHealthIdentityCodes(payload), {
          signal: controller.signal,
          onEvent: (incomingEvent) => {
            const event = redactHealthIdentityCodes(incomingEvent);
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
              assistantMessage.content = redactHealthIdentityCodes(
                assistantMessage.content + String(eventText(event))
              );
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
                assistantMessage.content = redactHealthIdentityCodes(
                  event.reply || event.content
                );
              }
              assistantMessage.decision = event.decision || assistantMessage.decision;
              assistantMessage.source = event.source || assistantMessage.source;
              assistantMessage.contextSources = Array.isArray(event.context_sources) ? event.context_sources : [];
              assistantMessage.recordResolution = event.record_resolution || null;
              if (event.next_active_record_context) {
                this.setActiveRecordContext(event.next_active_record_context);
              }
              if (event.record_resolution) {
                assistantMessage.recordSensitive = true;
                assistantMessage.contextRecordIds = Array.isArray(
                  event.record_resolution.anchor_record_ids
                )
                  ? event.record_resolution.anchor_record_ids
                  : [];
                assistantMessage.contextOwnerId =
                  positiveIntegerOrNull(event.record_resolution.owner?.id);
                assistantMessage.requestRecordContext =
                  normalizeActiveRecordContext(
                    event.next_active_record_context
                  ) || assistantMessage.requestRecordContext;
                userMessage.recordSensitive = true;
                userMessage.contextRecordIds = [...assistantMessage.contextRecordIds];
                userMessage.contextOwnerId = assistantMessage.contextOwnerId;
              } else if (event.auto_selected_records === true) {
                assistantMessage.recordSensitive = true;
                assistantMessage.contextRecordIds = Array.isArray(
                  event.selected_record_ids
                )
                  ? event.selected_record_ids
                  : [];
                userMessage.recordSensitive = true;
                userMessage.contextRecordIds = [
                  ...assistantMessage.contextRecordIds,
                ];
              } else if (assistantMessage.kind !== "analysis") {
                assistantMessage.recordSensitive = false;
                userMessage.recordSensitive = false;
              }
              if (!assistantMessage.recordSensitive) {
                this.summary = redactHealthIdentityCodes(
                  event.summary || this.summary
                );
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
        assistantMessage.errorCode = redactHealthIdentityCodes(
          error?.code || (cancelled ? "CANCELLED" : "")
        );
        assistantMessage.errorMessage = cancelled ? "已取消本次生成" : errorText(error);
        if (assistantMessage.kind === "analysis" && !retryable) {
          assistantMessage.retryRecords = [];
          this.resetAvailableRecords();
        } else if (assistantMessage.recordSensitive) {
          assistantMessage.retryRecordIds = retryable
            ? [...(assistantMessage.contextRecordIds || [])]
            : [];
          assistantMessage.retryOwnerId = retryable
            ? assistantMessage.contextOwnerId || null
            : null;
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
          this.persist();
        }
      }
    },

    cancelActive() {
      this.activeController?.abort?.();
    },
  },
});
