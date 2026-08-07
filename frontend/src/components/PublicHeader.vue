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
.public-site-header{grid-template-columns:minmax(0,1fr) auto minmax(0,1fr)}.public-site-header>.portal-brand{justify-self:start}.public-site-header>.portal-nav{display:flex!important;justify-self:center}.public-site-header>.portal-actions{justify-self:end}@media(max-width:1180px){.public-site-header .secondary-nav{display:none}.public-site-header>.portal-nav{gap:24px}}@media(max-width:820px){.public-site-header{grid-template-columns:minmax(0,1fr) auto;height:auto;min-height:64px;padding-top:8px;padding-bottom:8px}.public-site-header>.portal-nav{grid-column:1/-1;grid-row:2;width:100%;justify-content:center;gap:30px}.public-site-header>.portal-nav a{padding:8px 0}.public-site-header>.portal-nav a::after{bottom:2px}}@media(max-width:620px){.public-site-header{padding-inline:12px}.public-site-header>.portal-brand strong{display:none}.public-site-header>.portal-actions{gap:3px}.public-site-header>.portal-actions :deep(.el-button){padding-inline:9px}}
</style>
