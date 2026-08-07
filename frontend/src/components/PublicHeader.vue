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
  column-gap: clamp(12px, 2vw, 32px);
  padding-inline: clamp(24px, 2.5vw, 52px);
}

.public-site-header > .portal-brand {
  min-width: 0;
  justify-self: start;
}

.public-site-header > .portal-nav {
  display: flex !important;
  justify-self: center;
}

.public-site-header > .portal-actions {
  min-width: 0;
  justify-self: end;
  white-space: nowrap;
}

/* Keep every destination available. Once the three desktop columns no longer
   fit comfortably, move the complete navigation to its own centered row. */
@media (max-width: 1919px) {
  .public-site-header {
    grid-template-columns: minmax(0, 1fr) auto;
    height: auto;
    min-height: 64px;
    padding-top: 8px;
    padding-bottom: 8px;
  }

  .public-site-header > .portal-nav {
    grid-column: 1 / -1;
    grid-row: 2;
    width: 100%;
    justify-content: center;
    gap: 30px;
  }

  .public-site-header > .portal-nav a {
    padding: 8px 0;
  }

  .public-site-header > .portal-nav a::after {
    bottom: 2px;
  }
}

@media (max-width: 720px) {
  .public-site-header {
    padding-inline: 12px;
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
    gap: 2px 8px;
  }

  .public-site-header > .portal-nav a {
    min-width: 0;
    text-align: center;
  }
}
</style>
