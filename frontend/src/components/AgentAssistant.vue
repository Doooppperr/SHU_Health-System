<template>
  <aside
    v-if="aiStore.isOpen"
    id="ai-chat-panel"
    class="agent-panel"
    :class="{ overlay: overlayMode }"
    aria-label="HealthDoc Agent"
  >
    <header>
      <div>
        <h2>HealthDoc Agent</h2>
        <p>可读取档案、比较套餐并在确认后执行任务</p>
      </div>
      <div class="header-actions">
        <el-button size="small" :disabled="agent.isSending" @click="clearAgent">清空</el-button>
        <el-button size="small" @click="aiStore.setOpen(false)">关闭</el-button>
      </div>
    </header>

    <main ref="messageArea">
      <section v-if="!agent.messages.length" class="welcome">
        <strong>告诉我你的目标</strong>
        <p>例如：分析最近报告、解释血糖趋势、比较两项套餐，或查询预约状态。</p>
      </section>

      <article
        v-for="message in agent.messages"
        :key="message.id"
        class="message"
        :class="[message.role, { failed: message.failed }]"
      >
        <span>{{ message.role === "user" ? "你" : "Agent" }}</span>
        <p>{{ message.content || (message.streaming ? "正在处理…" : "") }}</p>
      </article>

      <section v-if="agent.activity.length" class="activity-card">
        <h3>任务轨迹</h3>
        <div v-for="item in agent.activity" :key="item.id" class="activity-row">
          <span>{{ item.type === "tool" ? "工具" : item.type === "receipt" ? "回执" : "证据" }}</span>
          <strong>{{ item.name }}</strong>
          <em v-if="item.status">{{ item.status === "completed" ? "完成" : item.status === "failed" ? "失败" : "执行中" }}</em>
        </div>
      </section>

      <section
        v-for="action in agent.pendingActions"
        :key="action.action_id"
        class="approval-card"
      >
        <h3>{{ action.summary?.title || "需要确认操作" }}</h3>
        <dl>
          <template v-for="(value, key) in action.summary" :key="key">
            <dt v-if="key !== 'title'">{{ key }}</dt>
            <dd v-if="key !== 'title'">{{ value }}</dd>
          </template>
        </dl>
        <div>
          <el-button
            size="small"
            :disabled="agent.isSending"
            @click="agent.decide(action.action_id, 'reject')"
          >拒绝</el-button>
          <el-button
            size="small"
            type="primary"
            :loading="agent.isSending"
            @click="agent.decide(action.action_id, 'approve')"
          >确认执行</el-button>
        </div>
      </section>

      <p v-if="agent.statusText" class="status">{{ agent.statusText }}</p>
      <el-alert v-if="agent.lastError" type="error" :closable="false" :title="agent.lastError" />
    </main>

    <form @submit.prevent="submit">
      <el-input
        v-model="input"
        type="textarea"
        :rows="3"
        maxlength="4000"
        show-word-limit
        placeholder="描述你希望 Agent 完成的任务"
        @keydown.ctrl.enter="submit"
      />
      <div class="composer-actions">
        <small>涉及预约、取消和工单时必须由你确认</small>
        <el-button v-if="agent.isSending" @click="agent.cancel()">停止</el-button>
        <el-button v-else type="primary" native-type="submit" :disabled="!input.trim()">发送</el-button>
      </div>
    </form>
  </aside>
</template>

<script setup>
import { nextTick, onMounted, ref, watch } from "vue";

import { useAgentStore } from "../stores/agent";
import { useAiChatStore } from "../stores/aiChat";
import { useAuthStore } from "../stores/auth";

defineProps({ overlayMode: { type: Boolean, default: false } });
const agent = useAgentStore();
const aiStore = useAiChatStore();
const auth = useAuthStore();
const input = ref("");
const messageArea = ref(null);

async function scrollBottom() {
  await nextTick();
  if (messageArea.value) messageArea.value.scrollTop = messageArea.value.scrollHeight;
}

async function submit() {
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  await agent.send(message);
  await scrollBottom();
}

async function clearAgent() {
  await agent.clear();
}

onMounted(() => agent.initialize(auth.user?.id));
watch(
  () => [agent.messages.length, agent.messages.at(-1)?.content, agent.activity.length],
  scrollBottom
);
</script>

<style scoped>
.agent-panel {
  position: sticky;
  z-index: 1800;
  top: 0;
  display: flex;
  grid-column: 2;
  grid-row: 1;
  flex-direction: column;
  width: 100%;
  height: 100dvh;
  border-left: 1px solid var(--color-border, #d2d2d7);
  background: var(--color-canvas, #f5f5f7);
}
.agent-panel.overlay { position: fixed; right: 0; width: var(--ai-panel-width); box-shadow: -12px 0 36px rgb(29 29 31 / 16%); }
header { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 16px; border-bottom: 1px solid var(--color-border, #d2d2d7); background: #fff; }
h2, h3, p { margin: 0; }
header h2 { font-size: 18px; }
header p { margin-top: 4px; color: var(--color-text-secondary, #5f6368); font-size: 12px; }
.header-actions { display: flex; gap: 6px; }
main { flex: 1; min-height: 0; padding: 16px; overflow-y: auto; }
.welcome, .activity-card, .approval-card { margin-bottom: 14px; padding: 14px; border: 1px solid var(--color-border, #d2d2d7); border-radius: 12px; background: #fff; }
.welcome p { margin-top: 8px; color: var(--color-text-secondary, #5f6368); line-height: 1.6; }
.message { width: fit-content; max-width: 88%; margin: 12px 0; padding: 10px 12px; border: 1px solid var(--color-border, #d2d2d7); border-radius: 12px; background: #fff; }
.message.user { margin-left: auto; border-color: var(--color-accent, #0b7a6b); background: var(--color-accent-soft, #e5f3f0); }
.message > span { color: var(--color-accent-strong, #075e54); font-size: 12px; font-weight: 700; }
.message p { margin-top: 5px; white-space: pre-wrap; line-height: 1.65; }
.message.failed { border-color: var(--color-danger, #c9342f); }
.activity-card h3, .approval-card h3 { margin-bottom: 10px; font-size: 14px; }
.activity-row { display: grid; grid-template-columns: 48px 1fr auto; gap: 8px; padding: 7px 0; border-top: 1px solid #eee; font-size: 12px; }
.activity-row em { color: var(--color-text-secondary, #5f6368); font-style: normal; }
.approval-card { border-color: #e6a23c; }
.approval-card dl { display: grid; grid-template-columns: 110px 1fr; gap: 6px; font-size: 12px; }
.approval-card dt { color: var(--color-text-secondary, #5f6368); }
.approval-card dd { margin: 0; overflow-wrap: anywhere; }
.approval-card > div { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.status { margin: 10px 0; color: var(--color-text-secondary, #5f6368); font-size: 13px; }
form { padding: 12px 16px max(12px, env(safe-area-inset-bottom)); border-top: 1px solid var(--color-border, #d2d2d7); background: #fff; }
.composer-actions { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 8px; }
.composer-actions small { color: var(--color-text-secondary, #5f6368); }
@media (max-width: 860px) { .agent-panel, .agent-panel.overlay { inset: 0; width: 100%; } }
</style>
