<template>
  <el-badge :value="unread" :hidden="!unread" :max="99">
    <el-button class="notification-trigger" aria-label="打开站内通知" @click="open">
      通知
    </el-button>
  </el-badge>
  <Teleport to="body">
    <Transition name="notification-panel">
      <div v-if="visible" class="notification-overlay" @mousedown.self="close">
        <aside
          class="notification-panel"
          role="dialog"
          aria-modal="true"
          aria-labelledby="notification-panel-title"
          @keydown.esc="close"
        >
          <header class="notification-header">
            <div>
              <strong id="notification-panel-title">站内通知</strong>
              <small>预约、付款、报告与服务提醒</small>
            </div>
            <button type="button" class="notification-close" aria-label="关闭站内通知" @click="close">×</button>
          </header>

          <div class="notification-body">
            <div class="notification-toolbar">
              <span>{{ unread ? `${unread} 条未读` : "已全部读完" }}</span>
              <el-button link type="primary" :disabled="!unread" @click="readAll">全部已读</el-button>
            </div>

            <div v-if="loading" class="notification-loading" role="status" aria-live="polite">
              <span aria-hidden="true"></span>
              正在加载通知…
            </div>
            <div v-else-if="loadError" class="notification-error" role="alert">
              <strong>通知暂时没有加载成功</strong>
              <p>{{ loadError }}</p>
              <el-button type="primary" plain @click="load">重新加载</el-button>
            </div>
            <div v-else class="notification-list">
              <article v-for="item in items" :key="item.id" :class="{ unread: !item.is_read }">
                <button type="button" @click="activate(item)">
                  <strong>{{ item.title }}</strong>
                  <p>{{ item.body }}</p>
                  <small>{{ formatTime(item.created_at) }}</small>
                </button>
              </article>
              <el-empty v-if="!items.length" description="暂无站内通知" />
            </div>

            <el-pagination
              v-if="!loading && !loadError && pagination.total > pagination.page_size"
              v-model:current-page="pagination.page"
              :page-size="pagination.page_size"
              :total="pagination.total"
              layout="prev, pager, next"
              @current-change="load"
            />
          </div>
        </aside>
      </div>
    </Transition>
  </Teleport>
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

function close() {
  visible.value = false;
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

.notification-overlay {
  position: fixed;
  z-index: 2400;
  inset: 0;
  display: flex;
  justify-content: flex-end;
  background: var(--color-overlay, rgba(15, 15, 17, 0.56));
}

.notification-panel {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  width: min(480px, calc(100vw - 16px));
  height: 100vh;
  height: 100dvh;
  min-height: 0;
  overflow: hidden;
  border-left: 1px solid var(--color-border, #3a3a3c);
  color: var(--color-text, #f5f5f7);
  background: var(--color-surface, #1c1c1e);
  box-shadow: -16px 0 38px rgb(0 0 0 / 24%);
}

.notification-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 76px;
  padding: max(18px, env(safe-area-inset-top)) max(22px, env(safe-area-inset-right)) 18px 22px;
  border-bottom: 1px solid var(--color-border, #3a3a3c);
  background: var(--color-surface, #1c1c1e);
}

.notification-header > div {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.notification-header strong {
  color: var(--color-text, #f5f5f7);
  font-size: 18px;
}

.notification-header small,
.notification-toolbar,
.notification-list small {
  color: var(--color-text-secondary, #b8b8bd);
}

.notification-close {
  display: grid;
  flex: 0 0 auto;
  width: 38px;
  height: 38px;
  padding: 0;
  place-items: center;
  border: 1px solid transparent;
  border-radius: 10px;
  color: var(--color-text-secondary, #b8b8bd);
  background: transparent;
  cursor: pointer;
  font: inherit;
  font-size: 25px;
  line-height: 1;
}

.notification-close:hover,
.notification-close:focus-visible {
  border-color: var(--color-border, #3a3a3c);
  color: var(--color-text, #f5f5f7);
  background: var(--color-surface-muted, #242426);
}

.notification-body {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  min-height: 0;
  padding: 18px 22px max(24px, env(safe-area-inset-bottom));
  overflow: hidden;
}

.notification-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.notification-loading,
.notification-list {
  min-height: 0;
  overflow-y: auto;
}

.notification-list {
  display: grid;
  align-content: start;
  gap: 10px;
  padding-right: 3px;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}

.notification-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--color-text-secondary, #b8b8bd);
}

.notification-loading span {
  width: 20px;
  height: 20px;
  border: 2px solid var(--color-border, #3a3a3c);
  border-top-color: var(--color-accent, #59cdb9);
  border-radius: 50%;
  animation: notification-spin 0.8s linear infinite;
}

.notification-list article {
  overflow: hidden;
  border: 1px solid var(--color-border, #3a3a3c);
  border-radius: 12px;
  background: var(--color-surface-elevated, #2c2c2e);
}

.notification-list article.unread {
  border-color: var(--color-accent, #59cdb9);
  background: var(--color-accent-soft, #183b36);
}

.notification-list button {
  width: 100%;
  padding: 14px;
  border: 0;
  color: var(--color-text, #f5f5f7);
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
  align-self: start;
  gap: 10px;
  justify-items: start;
  padding: 18px;
  border: 1px solid color-mix(in srgb, var(--color-danger, #c9342f) 44%, var(--color-border, #3a3a3c));
  border-radius: 12px;
  color: var(--color-text, #f5f5f7);
  background: color-mix(in srgb, var(--color-danger, #c9342f) 10%, var(--color-surface, #1c1c1e));
}

.notification-error p {
  margin: 0;
  color: var(--color-text-secondary, #b8b8bd);
}

.notification-body :deep(.el-pagination) {
  justify-content: center;
  padding-top: 16px;
}

.notification-panel-enter-active,
.notification-panel-leave-active {
  transition: background-color 0.2s ease;
}

.notification-panel-enter-active .notification-panel,
.notification-panel-leave-active .notification-panel {
  transition: transform 0.2s ease;
}

.notification-panel-enter-from,
.notification-panel-leave-to {
  background-color: transparent;
}

.notification-panel-enter-from .notification-panel,
.notification-panel-leave-to .notification-panel {
  transform: translateX(100%);
}

@keyframes notification-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 520px) {
  .notification-panel {
    width: 100vw;
    max-width: 100vw;
    border-left: 0;
  }

  .notification-header,
  .notification-body {
    padding-right: max(16px, env(safe-area-inset-right));
    padding-left: max(16px, env(safe-area-inset-left));
  }
}

@media (prefers-reduced-motion: reduce) {
  .notification-panel-enter-active,
  .notification-panel-leave-active,
  .notification-panel-enter-active .notification-panel,
  .notification-panel-leave-active .notification-panel {
    transition: none;
  }
}
</style>
