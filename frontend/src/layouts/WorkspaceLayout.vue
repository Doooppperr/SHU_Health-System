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
        </router-link>
      </nav>

      <div class="workspace-sidebar-footer">
        <div class="workspace-user">
          <span class="workspace-avatar">{{ userInitial }}</span>
          <span>
            <strong>{{ authStore.user?.username || "用户" }}</strong>
            <small>{{ roleName }}</small>
          </span>
        </div>
        <button type="button" class="workspace-logout" @click="logout">退出登录</button>
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
          <AppearanceQuickControls />
          <router-link class="workspace-portal-link" to="/">返回门户</router-link>
          <span class="workspace-role-badge">{{ roleName }}</span>
        </div>
      </header>

      <main id="main-content" class="workspace-content" tabindex="-1">
        <router-view />
      </main>
    </section>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { useAuthStore } from "../stores/auth";
import { useAppearanceStore } from "../stores/appearance";
import { dashboardRouteForRole, roleLabel } from "../utils/roles";
import AppearanceQuickControls from "../components/AppearanceQuickControls.vue";

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

const menus = {
  user: [
    { name: "dashboard", label: "健康总览", icon: "总" },
    { name: "timeline", label: "健康时间线", icon: "线" },
    { name: "measurements", label: "日常测量", icon: "测" },
    { name: "trends", label: "指标趋势", icon: "趋" },
    { name: "friends", label: "亲友授权", icon: "友" },
    { name: "institutions", label: "体检机构", icon: "院" },
    { name: "my-comments", label: "我的评论", icon: "评" },
    { name: "profile", label: "个人资料", icon: "我" },
  ],
  institution_admin: [
    { name: "org-dashboard", label: "运营总览", icon: "总" },
    { name: "org-profile", label: "机构资料", icon: "资" },
    { name: "org-gallery", label: "机构相册", icon: "图" },
    { name: "org-packages", label: "体检套餐", icon: "套" },
    { name: "org-reports", label: "体检报告", icon: "报" },
  ],
  admin: [
    { name: "admin-dashboard", label: "系统总览", icon: "总" },
    { name: "admin-institutions", label: "机构与套餐", icon: "院" },
    { name: "admin-invites", label: "邀请码", icon: "邀" },
    { name: "admin-users", label: "用户与角色", icon: "用" },
    { name: "admin-comments", label: "评论审核", icon: "评" },
  ],
};

const workspaceType = computed(() => authStore.user?.role || "user");
const workspaceName = computed(() => {
  if (workspaceType.value === "admin") return "系统管理后台";
  if (workspaceType.value === "institution_admin") return "机构运营后台";
  return "个人健康中心";
});
const menuItems = computed(() => menus[workspaceType.value] || menus.user);
const roleName = computed(() => roleLabel(authStore.user?.role));
const userInitial = computed(() => (authStore.user?.username || "U").slice(0, 1).toUpperCase());
const homeRoute = computed(() => dashboardRouteForRole(authStore.user?.role));
const pageTitle = computed(() => route.meta.title || workspaceName.value);
const pageEyebrow = computed(() => route.meta.eyebrow || workspaceName.value);

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

function logout() {
  authStore.logout();
  router.replace({ name: "public-home" });
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

onMounted(() => {
  mobileMediaQuery = window.matchMedia("(max-width: 980px)");
  careMobileMediaQuery = window.matchMedia("(max-width: 1180px)");
  syncMobileViewport();
  mobileMediaQuery.addEventListener?.("change", syncMobileViewport);
  careMobileMediaQuery.addEventListener?.("change", syncMobileViewport);
});

onBeforeUnmount(() => {
  mobileMediaQuery?.removeEventListener?.("change", syncMobileViewport);
  careMobileMediaQuery?.removeEventListener?.("change", syncMobileViewport);
});
</script>
