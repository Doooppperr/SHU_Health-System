<template>
  <div class="public-catalog-shell">
    <PublicHeader />
    <main id="main-content" class="public-catalog-main" tabindex="-1"><router-view /></main>
    <footer class="public-catalog-footer">
      <span>{{ contact.address }} · {{ contact.phone }} · {{ contact.email }}</span>
      <small><IcpFilingLink /> · {{ buildLabel() }}</small>
    </footer>
  </div>
</template>

<script setup>
import { onMounted, reactive } from "vue";
import { fetchPublicContact } from "../api/public";
import IcpFilingLink from "../components/IcpFilingLink.vue";
import PublicHeader from "../components/PublicHeader.vue";
import { buildLabel } from "../utils/buildInfo";

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
.public-catalog-main{width:min(1240px,calc(100% - 36px));margin:0 auto;padding:28px 0 52px}.public-catalog-footer{display:flex;justify-content:space-between;gap:16px;padding:22px clamp(18px,5vw,72px);border-top:1px solid var(--color-border);color:var(--color-text-muted);background:var(--color-surface)}
@media(max-width:760px){.public-catalog-footer{flex-direction:column}}
</style>
