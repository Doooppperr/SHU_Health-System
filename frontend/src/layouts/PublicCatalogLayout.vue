<template>
  <div class="public-catalog-shell">
    <header class="public-catalog-header">
      <router-link class="portal-brand" to="/"><span>H</span><strong>康康健健 HealthDoc</strong></router-link>
      <nav aria-label="访客导航">
        <router-link :to="{name:'public-institutions'}">机构与套餐</router-link>
        <router-link to="/#join-us">加入我们</router-link>
      </nav>
      <div>
        <AppearanceQuickControls />
        <el-button v-if="!auth.accessToken" @click="router.push({name:'login'})">登录</el-button>
        <el-button v-if="!auth.accessToken" type="primary" @click="router.push({name:'register'})">注册</el-button>
        <el-button v-else type="primary" @click="router.push(dashboardRouteForRole(auth.user?.role))">进入工作台</el-button>
      </div>
    </header>
    <main id="main-content" class="public-catalog-main" tabindex="-1"><router-view /></main>
    <footer class="public-catalog-footer">
      <span>{{ contact.address }} · {{ contact.phone }} · {{ contact.email }}</span>
      <small>{{ buildLabel() }}</small>
    </footer>
  </div>
</template>

<script setup>
import { onMounted, reactive } from "vue";
import { useRouter } from "vue-router";
import { fetchPublicContact } from "../api/public";
import AppearanceQuickControls from "../components/AppearanceQuickControls.vue";
import { useAuthStore } from "../stores/auth";
import { dashboardRouteForRole } from "../utils/roles";
import { buildLabel } from "../utils/buildInfo";

const router = useRouter();
const auth = useAuthStore();
const contact = reactive({
  address: "上海市宝山区上大路99号",
  phone: "021-114514",
  email: "shucs666@shu.edu.cn",
});

onMounted(async () => {
  try {
    const { data } = await fetchPublicContact();
    const item = data?.item || data;
    if (item?.address) contact.address = item.address;
    if (item?.phone) contact.phone = item.phone;
    if (item?.email) contact.email = item.email;
  } catch {
    // Keep the fixed platform contact as the visitor-safe fallback.
  }
});
</script>

<style scoped>
.public-catalog-shell{min-height:100vh;background:var(--color-page,#f5f8f7)}
.public-catalog-header{position:sticky;top:0;z-index:20;display:flex;align-items:center;justify-content:space-between;gap:22px;padding:14px clamp(18px,5vw,72px);border-bottom:1px solid var(--color-border);background:color-mix(in srgb,var(--color-surface) 94%,transparent);backdrop-filter:blur(16px)}
.public-catalog-header nav,.public-catalog-header>div{display:flex;align-items:center;gap:12px}.public-catalog-header nav a{color:var(--color-text-muted);font-weight:700;text-decoration:none}.public-catalog-header nav a.router-link-active{color:var(--workspace-accent,#116e63)}
.public-catalog-main{width:min(1240px,calc(100% - 36px));margin:0 auto;padding:28px 0 52px}.public-catalog-footer{display:flex;justify-content:space-between;gap:16px;padding:22px clamp(18px,5vw,72px);border-top:1px solid var(--color-border);color:var(--color-text-muted);background:var(--color-surface)}
@media(max-width:760px){.public-catalog-header{flex-wrap:wrap}.public-catalog-header nav{order:3;width:100%}.public-catalog-footer{flex-direction:column}}
</style>
