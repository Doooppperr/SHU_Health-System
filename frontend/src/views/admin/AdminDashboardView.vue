<template>
  <div class="workspace-page admin-dashboard">
    <section class="welcome-panel welcome-panel--admin"><div><p>平台账号、机构与资金治理</p><h2>系统运行概览</h2><span>管理员不接触报告、指标、日常测量或健康时间线。</span></div><span class="admin-shield">ADMIN</span></section>
    <section class="metric-grid" v-loading="loading"><article v-for="m in metrics" :key="m.label" class="metric-card"><span class="metric-icon">{{m.icon}}</span><div><small>{{m.label}}</small><strong>{{m.value}}</strong><p>{{m.note}}</p></div></article></section>
    <section class="platform-finance-strip">
      <article><small>平台托管资金</small><strong>¥ {{ money(finance.platform_custody) }}</strong></article>
      <article><small>累计服务费</small><strong>¥ {{ money(finance.platform_fee) }}</strong></article>
      <article><small>待结算净额</small><strong>¥ {{ money(finance.pending_settlement) }}</strong></article>
      <article><small>待机构退款</small><strong>{{ finance.refund_required_count || 0 }} 笔</strong><span>{{ finance.suspended_institution_count || 0 }} 家已暂停</span></article>
    </section>
    <section class="admin-shortcut-grid"><button v-for="a in actions" :key="a.name" @click="router.push({name:a.name})"><span>{{a.icon}}</span><div><strong>{{a.title}}</strong><small>{{a.note}}</small></div><b>→</b></button></section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { fetchAdminDashboard } from "../../api/dashboards";
import { fetchAdminFinanceSummary } from "../../api/admin";
const router=useRouter(),summary=ref({}),finance=ref({}),loading=ref(false);const money=(value)=>Number(value||0).toFixed(2);
const metrics=computed(()=>[{label:"全部账号",value:summary.value.account_count||0,icon:"账",note:"不含健康内容"},{label:"普通用户",value:summary.value.regular_user_count||0,icon:"用",note:"可停用或级联删除"},{label:"启用机构",value:summary.value.active_institution_count||0,icon:"院",note:`共 ${summary.value.institution_count||0} 家`},{label:"待审套餐",value:summary.value.pending_package_review_count||0,icon:"审",note:"机构套餐变更申请"}]);
const actions=[{name:"admin-finance",icon:"款",title:"平台财务",note:"查看托管、结算、服务费与退款"},{name:"admin-package-reviews",icon:"审",title:"套餐审核",note:"通过或驳回机构申请"},{name:"admin-institutions",icon:"院",title:"机构管理",note:"创建分院及其唯一机构账号"},{name:"admin-complaints",icon:"诉",title:"投诉与退款",note:"认定责任并处理退款"},{name:"admin-users",icon:"账",title:"账号管理",note:"停用、恢复和删除"},{name:"admin-comments",icon:"评",title:"评论审核",note:"处罚违规账号与处理申诉"}];
onMounted(async()=>{loading.value=true;try{const[d,f]=await Promise.all([fetchAdminDashboard(),fetchAdminFinanceSummary()]);summary.value=d.data.summary||{};finance.value=f.data.summary||{}}finally{loading.value=false}});
</script>

<style scoped>
.admin-dashboard{display:grid;gap:18px}.platform-finance-strip{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.platform-finance-strip article{padding:18px;border:1px solid #e0e5ec;border-radius:14px;background:#fff}.platform-finance-strip small,.platform-finance-strip span{display:block;color:#72808d}.platform-finance-strip strong{display:block;margin:7px 0;color:#223a58;font-size:23px}@media(max-width:900px){.platform-finance-strip{grid-template-columns:repeat(2,1fr)}}@media(max-width:560px){.platform-finance-strip{grid-template-columns:1fr}}
</style>
