<template>
  <div class="workspace" :class="`workspace--${workspaceType}`">
    <aside
      ref="sidebarRef"
      class="workspace-sidebar"
      :class="{ 'is-open': mobileMenuOpen }"
      aria-label="工作台侧栏"
      :aria-hidden="mobileViewport && !mobileMenuOpen ? 'true' : undefined"
      :aria-modal="mobileViewport && mobileMenuOpen ? 'true' : undefined"
      :role="mobileViewport && mobileMenuOpen ? 'dialog' : undefined"
      :inert="mobileViewport && !mobileMenuOpen ? '' : undefined"
      @keydown="handleSidebarKeydown"
    >
      <router-link class="workspace-brand" :to="homeRoute" @click="closeMenu">
        <span class="workspace-brand-mark">H</span>
        <span>
          <strong>康康健健 HealthDoc</strong>
          <small>{{ workspaceName }}</small>
        </span>
      </router-link>

      <nav class="workspace-nav" aria-label="工作台导航">
        <router-link
          v-for="item in menuItems"
          :key="item.name"
          :to="{ name: item.name }"
          class="workspace-nav-item"
          @click="closeMenu"
        >
          <span class="workspace-nav-icon">{{ item.icon }}</span>
          <span>{{ item.label }}</span>
          <el-badge v-if="item.badge" :value="item.badge" :max="99" style="margin-left:auto" />
        </router-link>
      </nav>

      <div class="workspace-sidebar-footer">
        <div class="workspace-user">
          <span class="workspace-avatar">{{ userInitial }}</span>
          <span>
            <strong>{{ authStore.user?.username || authStore.user?.display_name || "用户" }}</strong>
            <small>{{ roleName }}</small>
          </span>
        </div>
        <div v-if="workspaceType === 'user'" class="workspace-session-actions">
          <el-dropdown trigger="click" placement="top-start" @command="switchRelatedAccount">
            <button
              type="button"
              class="workspace-session-button"
              :disabled="accountSwitchingId !== null"
              aria-label="切换关联账号"
            >
              <span>切换账号</span><span aria-hidden="true">⌃</span>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item disabled>当前：{{ currentAccountName }}</el-dropdown-item>
                <el-dropdown-item
                  v-for="relation in switchableRelations"
                  :key="relation.id"
                  :command="relation.id"
                  divided
                  :disabled="accountSwitchingId === relation.id"
                >
                  切换至 {{ relationDisplayName(relation) }}
                </el-dropdown-item>
                <el-dropdown-item v-if="!switchableRelations.length" disabled divided>
                  暂无可切换的关联账号
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <button type="button" class="workspace-logout" @click="logout">退出登录</button>
        </div>
        <button v-else type="button" class="workspace-logout" @click="logout">退出登录</button>
        <small class="workspace-build">{{ buildLabel() }}</small>
      </div>
    </aside>

    <button
      v-if="mobileMenuOpen"
      class="workspace-mask"
      type="button"
      aria-label="关闭导航"
      @click="closeMenu"
    />

    <section class="workspace-stage">
      <header class="workspace-topbar">
        <button class="workspace-menu-button" type="button" aria-label="打开导航" @click="mobileMenuOpen = true">
          <span />
          <span />
          <span />
        </button>
        <div>
          <p>{{ pageEyebrow }}</p>
          <h1>{{ pageTitle }}</h1>
        </div>
        <div class="workspace-top-actions">
          <NotificationCenter v-if="workspaceType === 'user'" />
          <AiAssistantLauncher />
          <AppearanceQuickControls />
          <router-link class="workspace-portal-link" to="/">返回门户</router-link>
          <span v-if="workspaceType !== 'user'" class="workspace-role-badge">{{ roleName }}</span>
        </div>
      </header>

      <main id="main-content" class="workspace-content" tabindex="-1">
        <div
          v-if="workspaceType === 'institution_admin' && authStore.user?.must_change_initial_password"
          class="initial-password-banner"
        >
          <span><strong>当前仍在使用平台发放的初始密码。</strong> 为保护机构账号，请尽快完成邮箱验证并修改密码。</span>
          <el-button size="small" type="warning" @click="router.push({ name: 'org-profile' })">
            去修改密码
          </el-button>
        </div>
        <BasicProfileGate v-if="workspaceType === 'user'" />
        <router-view />
      </main>
    </section>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";

import { useAuthStore } from "../stores/auth";
import { useAppearanceStore } from "../stores/appearance";
import { dashboardRouteForRole, roleLabel } from "../utils/roles";
import AiAssistantLauncher from "../components/AiAssistantLauncher.vue";
import NotificationCenter from "../components/NotificationCenter.vue";
import AppearanceQuickControls from "../components/AppearanceQuickControls.vue";
import BasicProfileGate from "../components/BasicProfileGate.vue";
import { fetchUnreadCommentReplyCount } from "../api/comments";
import { fetchFriends } from "../api/friends";
import { buildLabel } from "../utils/buildInfo";

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const appearanceStore = useAppearanceStore();
const mobileMenuOpen = ref(false);
const mobileViewport = ref(false);
const sidebarRef = ref(null);
let lastFocusedElement = null;
let mobileMediaQuery = null;
let careMobileMediaQuery = null;
const unreadReplies = ref(0);
const relatedAccounts = ref([]);
const accountSwitchingId = ref(null);

const menus = {
  user: [
    { name: "dashboard", label: "健康总览", icon: "总" },
    { name: "timeline", label: "健康时间线", icon: "线" },
    { name: "health-data", label: "体检数据", icon: "检" },
    { name: "trends", label: "健康趋势", icon: "趋" },
    { name: "friends", label: "亲友授权", icon: "友" },
    { name: "institutions", label: "体检机构", icon: "院" },
    { name: "appointments", label: "体检预约", icon: "约" },
    { name: "my-comments", label: "我的评论", icon: "评" },
    { name: "profile", label: "个人资料", icon: "我" },
  ],
  institution_admin: [
    { name: "org-dashboard", label: "运营总览", icon: "总" },
    { name: "org-profile", label: "机构资料", icon: "资" },
    { name: "org-comments", label: "用户评价", icon: "评" },
    { name: "org-packages", label: "体检套餐", icon: "套" },
    { name: "org-reports", label: "体检管理", icon: "检" },
    { name: "org-complaints", label: "投诉处理", icon: "诉" },
    { name: "org-package-reviews", label: "信息审核", icon: "审" },
  ],
  admin: [
    { name: "admin-dashboard", label: "系统总览", icon: "总" },
    { name: "admin-institutions", label: "机构与套餐", icon: "院" },
    { name: "admin-users", label: "用户与角色", icon: "用" },
    { name: "admin-complaints", label: "投诉记录", icon: "诉" },
    { name: "admin-comments", label: "评论审核", icon: "评" },
    { name: "admin-package-reviews", label: "机构审核记录", icon: "审" },
    { name: "admin-agent-ops", label: "Agent 运营", icon: "智" },
  ],
};

const workspaceType = computed(() => authStore.user?.role || "user");
const workspaceName = computed(() => {
  if (workspaceType.value === "admin") return "系统管理后台";
  if (workspaceType.value === "institution_admin") return "机构运营后台";
  return "个人健康中心";
});
const menuItems = computed(() => (menus[workspaceType.value] || menus.user).map((item) => (
  item.name === "my-comments" ? { ...item, badge: unreadReplies.value } : item
)));
const roleName = computed(() => roleLabel(authStore.user?.role));
const userInitial = computed(() => (authStore.user?.username || authStore.user?.display_name || "U").slice(0, 1).toUpperCase());
const currentAccountName = computed(() => (
  authStore.user?.real_name
  || authStore.user?.display_name
  || authStore.user?.username
  || "用户"
));
const switchableRelations = computed(() => {
  return relatedAccounts.value.filter((item) => {
    const canSwitch = item.can_switch ?? item.relationship_status === "active";
    return canSwitch;
  });
});
const homeRoute = computed(() => dashboardRouteForRole(authStore.user?.role));
const pageTitle = computed(() => route.meta.title || workspaceName.value);
const pageEyebrow = computed(() => route.meta.eyebrow || workspaceName.value);

function relationDisplayName(relation) {
  const person = relation.counterparty || relation.friend_user || relation.user || {};
  return person.display_name || person.real_name || person.username || "亲友";
}

async function loadRelatedAccounts() {
  if (authStore.user?.role !== "user") {
    relatedAccounts.value = [];
    return;
  }
  try {
    const { data } = await fetchFriends();
    const items = data.items || [...(data.outgoing || []), ...(data.incoming || [])];
    relatedAccounts.value = [...new Map(items.map((item) => [item.id, item])).values()];
  } catch {
    relatedAccounts.value = [];
  }
}

async function switchRelatedAccount(relationId) {
  const relation = relatedAccounts.value.find((item) => item.id === Number(relationId));
  if (!relation || accountSwitchingId.value) return;
  accountSwitchingId.value = relation.id;
  try {
    await authStore.switchToFriend(relation);
    ElMessage.success(`已切换至 ${relationDisplayName(relation)} 的授权账号`);
    await router.replace({ name: "timeline" });
    await loadRelatedAccounts();
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || error?.message || "账号切换失败");
  } finally {
    accountSwitchingId.value = null;
  }
}

function closeMenu() {
  mobileMenuOpen.value = false;
}

function focusableSidebarItems() {
  if (!sidebarRef.value) return [];
  return [...sidebarRef.value.querySelectorAll("a[href], button:not([disabled])")].filter(
    (element) => element instanceof HTMLElement && !element.hasAttribute("inert")
  );
}

function handleSidebarKeydown(event) {
  if (!mobileViewport.value || !mobileMenuOpen.value) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeMenu();
    return;
  }
  if (event.key !== "Tab") return;
  const items = focusableSidebarItems();
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function syncMobileViewport() {
  const nextMobile = mobileMediaQuery?.matches === true
    || (appearanceStore.careMode && careMobileMediaQuery?.matches === true);
  mobileViewport.value = nextMobile;
  if (!nextMobile) mobileMenuOpen.value = false;
}

async function logout() {
  await authStore.secureLogout();
  await router.replace({ name: "login" });
}

watch(mobileMenuOpen, async (open) => {
  if (!mobileViewport.value) return;
  if (open) {
    lastFocusedElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    await nextTick();
    focusableSidebarItems()[0]?.focus();
  } else {
    await nextTick();
    lastFocusedElement?.focus();
  }
});

watch(() => route.fullPath, () => {
  lastFocusedElement = null;
  closeMenu();
});
watch(() => appearanceStore.careMode, syncMobileViewport);
watch(() => authStore.user?.id, loadRelatedAccounts);

onMounted(() => {
  mobileMediaQuery = window.matchMedia("(max-width: 980px)");
  careMobileMediaQuery = window.matchMedia("(max-width: 1180px)");
  syncMobileViewport();
  mobileMediaQuery.addEventListener?.("change", syncMobileViewport);
  careMobileMediaQuery.addEventListener?.("change", syncMobileViewport);
  if (authStore.user?.role === "user") fetchUnreadCommentReplyCount().then(({data}) => { unreadReplies.value = data.count || 0; }).catch(() => {});
  loadRelatedAccounts();
  window.addEventListener("healthdoc-comment-replies-read", clearUnreadReplies);
});

function clearUnreadReplies() { unreadReplies.value = 0; }

onBeforeUnmount(() => {
  mobileMediaQuery?.removeEventListener?.("change", syncMobileViewport);
  careMobileMediaQuery?.removeEventListener?.("change", syncMobileViewport);
  window.removeEventListener("healthdoc-comment-replies-read", clearUnreadReplies);
});
</script>

<style scoped>
.initial-password-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
  padding: 12px 16px;
  border: 1px solid #e6b85c;
  border-radius: 14px;
  background: #fff8e8;
  color: #7a5410;
}

.workspace-build {
  display: block;
  margin-top: 8px;
  color: var(--el-text-color-secondary);
  text-align: center;
}

.workspace-session-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-top: 12px;
}

.workspace-session-actions :deep(.el-dropdown) {
  width: 100%;
}

.workspace-session-button {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  padding: 9px;
  border: 1px solid var(--el-border-color);
  border-radius: 10px;
  background: transparent;
  color: var(--el-text-color-primary);
  cursor: pointer;
}

.workspace-session-button:hover,
.workspace-session-button:focus-visible {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
}

.workspace-session-actions .workspace-logout {
  margin-top: 0;
}

@media (max-width: 620px) {
  .workspace-session-actions {
    grid-template-columns: 1fr;
  }
}
</style>
