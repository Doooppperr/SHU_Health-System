<template>
  <main id="main-content" class="action-page" tabindex="-1">
    <section class="action-card">
      <p class="eyebrow">Agent 操作确认</p>
      <h1>{{ action?.summary?.title || "核对操作内容" }}</h1>
      <dl v-if="action">
        <template v-for="(value, key) in action.summary" :key="key">
          <dt v-if="key !== 'title'">{{ key }}</dt>
          <dd v-if="key !== 'title'">{{ value }}</dd>
        </template>
      </dl>
      <el-alert
        type="warning"
        :closable="false"
        title="确认前系统会重新检查账号权限和最新业务状态。"
      />
      <p v-if="message">{{ message }}</p>
      <div v-if="action?.status === 'pending'" class="actions">
        <el-button :disabled="submitting" @click="decide('reject')">拒绝</el-button>
        <el-button type="primary" :loading="submitting" @click="decide('approve')">确认执行</el-button>
      </div>
    </section>
  </main>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";

import { fetchAgentAction, streamAgentDecision } from "../api/agent";

const route = useRoute();
const action = ref(null);
const submitting = ref(false);
const message = ref("");

async function load() {
  try {
    action.value = (await fetchAgentAction(route.params.id)).data.item;
  } catch (error) {
    message.value = error.response?.data?.message || "没有找到可确认的操作";
  }
}

async function decide(decision) {
  submitting.value = true;
  try {
    const result = await streamAgentDecision(route.params.id, decision);
    action.value.status = result.status;
    message.value = decision === "approve" ? "操作已完成。" : "操作已拒绝。";
  } catch (error) {
    message.value = error.message || "操作没有完成";
  } finally {
    submitting.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.action-page { display: grid; min-height: 100vh; place-items: center; padding: 24px; background: #f5f5f7; }
.action-card { width: min(560px, 100%); padding: 28px; border: 1px solid #d2d2d7; border-radius: 16px; background: #fff; }
.eyebrow { color: #0b7a6b; font-weight: 700; }
h1 { margin: 8px 0 20px; }
dl { display: grid; grid-template-columns: 150px 1fr; gap: 8px; }
dt { color: #5f6368; }
dd { margin: 0; overflow-wrap: anywhere; }
.actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 22px; }
</style>
