<template>
  <header class="portal-header public-site-header">
    <router-link class="portal-brand" to="/"><span>H</span><strong>康康健健 HealthDoc</strong></router-link>
    <nav class="portal-nav" aria-label="公开页面导航">
      <router-link :to="{name:'public-institutions'}">机构与套餐</router-link>
      <router-link class="secondary-nav" :to="{path:'/',hash:'#features'}">核心能力</router-link>
      <router-link class="secondary-nav" :to="{path:'/',hash:'#process'}">使用流程</router-link>
      <router-link class="secondary-nav" :to="{path:'/',hash:'#privacy'}">隐私保护</router-link>
      <router-link class="secondary-nav" :to="{path:'/',hash:'#about'}">关于我们</router-link>
      <router-link :to="{path:'/',hash:'#join-us'}">加入我们</router-link>
    </nav>
    <div class="portal-actions">
      <AppearanceQuickControls />
      <template v-if="auth.accessToken && auth.user">
        <el-button type="primary" round @click="enterWorkspace"><span class="portal-action-full">进入工作台</span><span class="portal-action-short">工作台</span></el-button>
      </template>
      <template v-else>
        <el-button round @click="router.push({name:'login'})">登录</el-button>
        <el-button type="primary" round @click="router.push({name:'register'})"><span class="portal-action-full">免费注册</span><span class="portal-action-short">注册</span></el-button>
      </template>
    </div>
  </header>
</template>

<script setup>
import { useRouter } from "vue-router";
import AppearanceQuickControls from "./AppearanceQuickControls.vue";
import { useAuthStore } from "../stores/auth";
import { dashboardRouteForRole } from "../utils/roles";

const router = useRouter();
const auth = useAuthStore();
function enterWorkspace(){router.push(dashboardRouteForRole(auth.user?.role));}
</script>

<style scoped>
.public-site-header {
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  column-gap: clamp(14px, 1.5vw, 30px);
  padding-inline: clamp(20px, 2.25vw, 46px);
}

.public-site-header > .portal-brand {
  min-width: 0;
  justify-self: start;
}

.public-site-header > .portal-nav {
  display: flex !important;
  justify-self: center;
  gap: clamp(16px, 1.35vw, 26px);
}

.public-site-header > .portal-actions {
  min-width: 0;
  justify-self: end;
  white-space: nowrap;
}

/* A regular desktop, including common browser zoom levels, stays on one calm
   row. Only genuinely narrow viewports use the compact navigation rail. */
@media (max-width: 1360px) {
  .public-site-header {
    column-gap: 12px;
    padding-inline: 18px;
  }

  .public-site-header > .portal-brand {
    gap: 8px;
    font-size: 14px;
  }

  .public-site-header > .portal-nav {
    gap: clamp(12px, 1.2vw, 18px);
    font-size: 13px;
  }

  .public-site-header > .portal-actions {
    gap: 4px;
  }

  .public-site-header > .portal-actions :deep(.appearance-controls) {
    gap: 4px;
  }

  .public-site-header > .portal-actions :deep(.appearance-control),
  .public-site-header > .portal-actions :deep(.el-button) {
    padding-inline: 10px;
  }
}

@media (max-width: 1280px) {
  .public-site-header {
    grid-template-columns: minmax(0, 1fr) auto;
    height: auto;
    min-height: 96px;
    padding-top: 7px;
    padding-bottom: 6px;
    row-gap: 3px;
  }

  .public-site-header > .portal-nav {
    grid-column: 1 / -1;
    grid-row: 2;
    width: min(680px, 100%);
    justify-content: center;
    gap: clamp(18px, 3.2vw, 34px);
    padding-inline: 12px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--color-surface-muted) 55%, transparent);
  }

  .public-site-header > .portal-nav a {
    padding: 7px 0 9px;
  }

  .public-site-header > .portal-nav a::after {
    bottom: 4px;
  }
}

@media (max-width: 720px) {
  .public-site-header {
    padding-inline: 12px;
    min-height: 112px;
    padding-top: 5px;
    padding-bottom: 5px;
    row-gap: 3px;
  }

  .public-site-header > .portal-brand strong {
    display: none;
  }

  .public-site-header > .portal-actions {
    gap: 3px;
  }

  .public-site-header > .portal-actions :deep(.el-button) {
    padding-inline: 9px;
  }

  .public-site-header > .portal-nav {
    display: grid !important;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    width: 100%;
    gap: 0 8px;
    padding: 1px 6px;
    border-radius: 14px;
  }

  .public-site-header > .portal-nav a {
    min-width: 0;
    padding: 4px 2px 6px;
    text-align: center;
  }

  .public-site-header > .portal-nav a::after {
    bottom: 3px;
  }
}
</style>
