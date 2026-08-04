<template>
  <div class="workspace-page finance-admin" v-loading="loading">
    <section class="admin-finance-hero"><div><p>平台资金</p><h2>托管、服务费与退款治理</h2><span>这里只展示订单资金，不包含任何健康报告内容。</span></div></section>
    <section class="admin-finance-metrics">
      <article><small>平台托管资金</small><strong>¥ {{ amount(summary.platform_custody) }}</strong></article>
      <article><small>累计服务费</small><strong>¥ {{ amount(summary.platform_fee) }}</strong></article>
      <article><small>待结算净额</small><strong>¥ {{ amount(summary.pending_settlement) }}</strong></article>
      <article><small>待机构退款</small><strong>{{ summary.refund_required_count || 0 }} 笔</strong><p>{{ summary.suspended_institution_count || 0 }} 家分院已暂停</p></article>
    </section>
    <el-card shadow="never">
      <template #header><div class="finance-header"><strong>平台订单账本</strong><el-select v-model="status" clearable placeholder="全部状态" @change="loadOrders"><el-option v-for="item in statuses" :key="item.value" :label="item.label" :value="item.value" /></el-select></div></template>
      <el-table :data="orders" empty-text="暂无订单">
        <el-table-column prop="order_no" label="订单号" min-width="190" />
        <el-table-column label="机构" min-width="180"><template #default="{row}">{{ row.institution?.name }} · {{ row.institution?.branch_name }}</template></el-table-column>
        <el-table-column prop="subject_name" label="受检者" width="110" />
        <el-table-column label="金额" width="110"><template #default="{row}">¥ {{ amount(row.gross_amount) }}</template></el-table-column>
        <el-table-column label="服务费" width="100"><template #default="{row}">¥ {{ amount(row.fee_amount) }}</template></el-table-column>
        <el-table-column label="机构净额" width="110"><template #default="{row}">¥ {{ amount(row.net_amount) }}</template></el-table-column>
        <el-table-column label="状态" width="130"><template #default="{row}"><el-tag :type="row.fund_status === 'refund_required' ? 'danger' : row.fund_status === 'settled' ? 'success' : 'warning'">{{ row.fund_status_label }}</el-tag></template></el-table-column>
        <el-table-column label="处理期限" min-width="180"><template #default="{row}">{{ datetime(row.refund_due_at || row.settlement_due_at) }}</template></el-table-column>
        <el-table-column label="投诉/账本" min-width="160"><template #default="{row}"><span v-if="row.complaint_id">投诉 #{{ row.complaint_id }} · </span>{{ row.ledger_changes?.length || 0 }} 笔资金变动</template></el-table-column>
      </el-table>
      <el-pagination v-if="pagination.total > pagination.page_size" v-model:current-page="pagination.page" :page-size="pagination.page_size" :total="pagination.total" layout="prev, pager, next" @current-change="loadOrders" />
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import { fetchAdminFinanceOrders, fetchAdminFinanceSummary } from "../../api/admin";
const summary=ref({}),orders=ref([]),loading=ref(false),status=ref("");const pagination=reactive({page:1,page_size:15,total:0,pages:0});
const statuses=[{value:"held",label:"平台托管"},{value:"scheduled",label:"待结算"},{value:"settled",label:"已到账"},{value:"refund_required",label:"待机构退款"},{value:"refunded",label:"已退款"}];
const amount=(value)=>Number(value||0).toFixed(2);const datetime=(value)=>value?new Date(value).toLocaleString("zh-CN"):"—";
async function loadSummary(){summary.value=(await fetchAdminFinanceSummary()).data.summary||{}}async function loadOrders(){const{data}=await fetchAdminFinanceOrders({page:pagination.page,page_size:pagination.page_size,status:status.value||undefined});orders.value=data.items||[];Object.assign(pagination,data.pagination||{})}
onMounted(async()=>{loading.value=true;try{await Promise.all([loadSummary(),loadOrders()])}catch(error){ElMessage.error(error?.response?.data?.message||"平台财务加载失败")}finally{loading.value=false}});
</script>

<style scoped>
.finance-admin{display:grid;gap:18px}.admin-finance-hero{padding:26px;border-radius:18px;color:#fff;background:linear-gradient(135deg,#263b63,#335a78)}.admin-finance-hero p{margin:0;font-weight:800}.admin-finance-hero h2{margin:6px 0}.admin-finance-hero span{color:#dce8f3}.admin-finance-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.admin-finance-metrics article{padding:20px;border:1px solid #e0e5ec;border-radius:15px;background:#fff}.admin-finance-metrics small,.admin-finance-metrics p{color:#72808d}.admin-finance-metrics strong{display:block;margin:8px 0;color:#223a58;font-size:25px}.admin-finance-metrics p{margin:0}.finance-header{display:flex;align-items:center;justify-content:space-between}.finance-header :deep(.el-select){width:160px}@media(max-width:900px){.admin-finance-metrics{grid-template-columns:repeat(2,1fr)}}@media(max-width:560px){.admin-finance-metrics{grid-template-columns:1fr}}
</style>
