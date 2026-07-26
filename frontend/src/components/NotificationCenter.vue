<template>
  <el-badge :value="unread" :hidden="!unread" :max="99">
    <el-button class="notification-trigger" aria-label="打开站内通知" @click="open">
      通知
    </el-button>
  </el-badge>
  <el-drawer v-model="visible" class="notification-drawer" size="480px">
    <template #header>
      <div class="notification-header">
        <strong>站内通知</strong>
        <small>预约、报告与空位提醒</small>
      </div>
    </template>
    <div class="notification-toolbar">
      <span>{{ unread ? `${unread} 条未读` : "已全部读完" }}</span>
      <el-button link type="primary" :disabled="!unread" @click="readAll">全部已读</el-button>
    </div>
    <div v-if="loadError" class="notification-error" role="alert">
      <strong>通知暂时没有加载成功</strong>
      <p>{{ loadError }}</p>
      <el-button type="primary" plain @click="load">重新加载</el-button>
    </div>
    <div v-else v-loading="loading" class="notification-list">
      <article v-for="item in items" :key="item.id" :class="{ unread: !item.is_read }">
        <button type="button" @click="activate(item)">
          <strong>{{ item.title }}</strong>
          <p>{{ item.body }}</p>
          <small>{{ formatTime(item.created_at) }}</small>
        </button>
      </article>
      <el-empty v-if="!loading && !items.length" description="暂无站内通知" />
    </div>
    <el-pagination
      v-if="!loadError && pagination.total > pagination.page_size"
      v-model:current-page="pagination.page"
      :page-size="pagination.page_size"
      :total="pagination.total"
      layout="prev, pager, next"
      @current-change="load"
    />
  </el-drawer>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import {
  fetchNotifications,
  fetchNotificationUnreadCount,
  markAllNotificationsRead,
  markNotificationRead,
} from "../api/notifications";

const router = useRouter();
const visible = ref(false);
const loading = ref(false);
const items = ref([]);
const unread = ref(0);
const loadError = ref("");
const pagination = reactive({ page: 1, page_size: 15, total: 0, pages: 0 });
const formatTime = (value) => value ? new Date(value).toLocaleString("zh-CN") : "";

function errorMessage(error) {
  return error?.response?.data?.message || "请检查网络后重试";
}

async function refreshCount() {
  try {
    unread.value = (await fetchNotificationUnreadCount()).data.unread_count || 0;
  } catch {
    unread.value = 0;
  }
}

async function load() {
  loading.value = true;
  loadError.value = "";
  try {
    const { data } = await fetchNotifications({ page: pagination.page, page_size: 15 });
    items.value = data.items || [];
    unread.value = data.unread_count || 0;
    Object.assign(pagination, data.pagination || {});
  } catch (error) {
    loadError.value = errorMessage(error);
  } finally {
    loading.value = false;
  }
}

async function open() {
  visible.value = true;
  pagination.page = 1;
  await load();
}

async function activate(item) {
  try {
    if (!item.is_read) {
      await markNotificationRead(item.id);
      item.is_read = true;
      unread.value = Math.max(0, unread.value - 1);
    }
    if (item.action_url) {
      visible.value = false;
      await router.push(item.action_url);
    }
  } catch (error) {
    loadError.value = errorMessage(error);
  }
}

async function readAll() {
  try {
    await markAllNotificationsRead();
    items.value.forEach((item) => { item.is_read = true; });
    unread.value = 0;
  } catch (error) {
    loadError.value = errorMessage(error);
  }
}

onMounted(refreshCount);
</script>

<style scoped>
.notification-trigger {
  min-width: 58px;
  border-radius: 999px;
}

.notification-header {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.notification-header strong {
  color: var(--color-text);
  font-size: 18px;
}

.notification-header small,
.notification-toolbar,
.notification-list small {
  color: var(--color-text-secondary);
}

.notification-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.notification-list {
  display: grid;
  align-content: start;
  gap: 10px;
  min-height: 160px;
}

.notification-list article {
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  background: var(--color-surface);
}

.notification-list article.unread {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}

.notification-list button {
  width: 100%;
  padding: 14px;
  border: 0;
  color: inherit;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.notification-list strong {
  display: block;
}

.notification-list p {
  margin: 7px 0;
  line-height: 1.65;
  overflow-wrap: anywhere;
}

.notification-error {
  display: grid;
  gap: 10px;
  justify-items: start;
  padding: 18px;
  border: 1px solid color-mix(in srgb, var(--color-danger) 44%, var(--color-border));
  border-radius: 12px;
  color: var(--color-text);
  background: color-mix(in srgb, var(--color-danger) 10%, var(--color-surface));
}

.notification-error p {
  margin: 0;
  color: var(--color-text-secondary);
}

:global(.notification-drawer) {
  max-width: calc(100vw - 16px);
  border-left: 1px solid var(--color-border);
}

:global(.notification-drawer .el-drawer__header) {
  min-height: 76px;
  margin-bottom: 0;
  padding: 18px 22px;
  border-bottom: 1px solid var(--color-border);
}

:global(.notification-drawer .el-drawer__body) {
  padding: 18px 22px 24px;
}
</style>
