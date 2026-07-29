<template>
  <main id="main-content" class="consent-page" tabindex="-1">
    <section class="consent-card">
      <p class="eyebrow">外部 Agent 授权</p>
      <h1>允许此客户端连接 HealthDoc？</h1>
      <p>客户端：{{ clientId }}</p>
      <h2>申请权限</h2>
      <ul>
        <li v-for="scope in scopes" :key="scope">{{ scopeLabels[scope] || scope }}</li>
      </ul>
      <el-alert
        type="warning"
        :closable="false"
        title="涉及预约、取消和人工工单的操作，仍需回到 HealthDoc 页面逐次确认。"
      />
      <p v-if="error" class="error">{{ error }}</p>
      <div class="actions">
        <el-button :disabled="submitting" @click="decide('reject')">拒绝</el-button>
        <el-button type="primary" :loading="submitting" @click="decide('approve')">允许</el-button>
      </div>
    </section>
  </main>
</template>

<script setup>
import { computed, ref } from "vue";
import { useRoute } from "vue-router";

import { authorizeOAuth } from "../api/oauth";

const route = useRoute();
const submitting = ref(false);
const error = ref("");
const clientId = computed(() => String(route.query.client_id || ""));
const scopes = computed(() => String(route.query.scope || "").split(/\s+/).filter(Boolean));
const scopeLabels = {
  "knowledge.read": "读取公共健康知识",
  "catalog.read": "查看机构与套餐",
  "records.read": "读取你授权的健康档案",
  "booking.read": "查看预约状态",
  "booking.write": "创建预约、取消或候补草稿",
  "support.write": "创建人工客服工单草稿",
};

async function decide(decision) {
  submitting.value = true;
  error.value = "";
  try {
    const response = await authorizeOAuth({ ...route.query, decision });
    window.location.assign(response.data.redirect_to);
  } catch (caught) {
    error.value = caught.response?.data?.error_description || caught.message || "授权没有完成";
  } finally {
    submitting.value = false;
  }
}
</script>

<style scoped>
.consent-page { display: grid; min-height: 100vh; place-items: center; padding: 24px; background: #f5f5f7; }
.consent-card { width: min(560px, 100%); padding: 28px; border: 1px solid #d2d2d7; border-radius: 16px; background: #fff; box-shadow: 0 12px 36px rgb(29 29 31 / 12%); }
.eyebrow { color: #0b7a6b; font-weight: 700; }
h1 { margin: 8px 0 18px; font-size: 24px; }
h2 { margin-top: 22px; font-size: 16px; }
li { margin: 8px 0; }
.actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 22px; }
.error { color: #c9342f; }
</style>
