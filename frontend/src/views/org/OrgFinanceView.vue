<template>
  <div class="workspace-page finance-page" v-loading="loading">
    <el-alert
      v-if="summary.operations_suspended"
      type="error"
      show-icon
      :closable="false"
      title="分院运营已暂停"
      :description="`${summary.operations_suspension_reason || '存在逾期退款'}。完成全部待退款订单后将自动恢复。`"
    />
    <section class="finance-hero">
      <div><p>资金账户</p><h2>收款与退款</h2><span>每笔到账均已扣除 2.5% 平台服务费。</span></div>
    </section>
    <section class="finance-metrics">
      <article><small>可用余额</small><strong>¥ {{ amount(summary.available_balance) }}</strong><p>到账增加，退款减少</p></article>
      <article><small>累计到账</small><strong>¥ {{ amount(summary.cumulative_credited) }}</strong><p>历史净结算总额</p></article>
      <article><small>待结算</small><strong>¥ {{ amount(summary.pending_settlement) }}</strong><p>报告发布七日后到账</p></article>
      <article><small>累计退款</small><strong>¥ {{ amount(summary.cumulative_refunded) }}</strong><p>{{ summary.refund_required_count || 0 }} 笔等待处理</p></article>
    </section>
    <el-card shadow="never">
      <template #header><div class="finance-header"><strong>订单明细</strong><el-select v-model="status" clearable placeholder="全部状态" @change="loadOrders"><el-option v-for="item in statuses" :key="item.value" :label="item.label" :value="item.value" /></el-select></div></template>
      <el-table :data="orders" empty-text="暂无收款订单">
        <el-table-column prop="order_no" label="订单号" min-width="190" />
        <el-table-column prop="subject_name" label="受检者" min-width="100" />
        <el-table-column prop="package_name" label="体检服务" min-width="160" />
        <el-table-column label="订单金额" width="110"><template #default="{row}">¥ {{ amount(row.gross_amount) }}</template></el-table-column>
        <el-table-column label="服务费" width="100"><template #default="{row}">¥ {{ amount(row.fee_amount) }}</template></el-table-column>
        <el-table-column label="净到账" width="110"><template #default="{row}">¥ {{ amount(row.net_amount) }}</template></el-table-column>
        <el-table-column label="资金状态" width="130"><template #default="{row}"><el-tag :type="tone(row.fund_status)">{{ row.fund_status_label }}</el-tag></template></el-table-column>
        <el-table-column label="报告发布日期" min-width="170"><template #default="{row}">{{ datetime(row.report_published_at) }}</template></el-table-column>
        <el-table-column label="结算/退款时间" min-width="185"><template #default="{row}">{{ datetime(row.refunded_at || row.settled_at || row.settlement_due_at) }}</template></el-table-column>
        <el-table-column label="操作" width="110" fixed="right"><template #default="{row}"><el-button v-if="['settled','refund_required'].includes(row.fund_status)" link type="danger" @click="refund(row)">退款</el-button><span v-else>—</span></template></el-table-column>
      </el-table>
      <el-pagination v-if="pagination.total > pagination.page_size" v-model:current-page="pagination.page" :page-size="pagination.page_size" :total="pagination.total" layout="prev, pager, next" @current-change="loadOrders" />
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { fetchOrgFinanceOrders, fetchOrgFinanceSummary, refundOrgFinanceOrder } from "../../api/org";

const summary = ref({}); const orders = ref([]); const loading = ref(false); const status = ref("");
const pagination = reactive({ page: 1, page_size: 15, total: 0, pages: 0 });
const statuses = [
  { value: "held", label: "平台托管" }, { value: "scheduled", label: "待结算" },
  { value: "settled", label: "已到账" }, { value: "refund_required", label: "待退款" },
  { value: "refunded", label: "已退款" },
];
const amount = (value) => Number(value || 0).toFixed(2);
const datetime = (value) => value ? new Date(value).toLocaleString("zh-CN") : "待报告发布";
const tone = (value) => ({ settled: "success", refund_required: "danger", refunded: "info", scheduled: "warning", held: "warning" })[value] || "info";
async function loadSummary() { summary.value = (await fetchOrgFinanceSummary()).data.summary || {}; }
async function loadOrders() { const { data } = await fetchOrgFinanceOrders({ page: pagination.page, page_size: pagination.page_size, status: status.value || undefined }); orders.value = data.items || []; Object.assign(pagination, data.pagination || {}); }
async function refund(row) {
  await ElMessageBox.confirm(`确认将订单 ${row.order_no} 的 ¥${amount(row.gross_amount)} 原路退回？`, "确认退款", { type: "warning", confirmButtonText: "确认退款" });
  await refundOrgFinanceOrder(row.id); ElMessage.success("退款已完成并原路退回"); await Promise.all([loadSummary(), loadOrders()]);
}
onMounted(async () => { loading.value = true; try { await Promise.all([loadSummary(), loadOrders()]); } catch (error) { ElMessage.error(error?.response?.data?.message || "财务数据加载失败"); } finally { loading.value = false; } });
</script>

<style scoped>
.finance-page{display:grid;gap:18px}.finance-hero{padding:26px;border:1px solid #dce8e6;border-radius:18px;background:linear-gradient(135deg,#edf8f5,#fff)}.finance-hero p{margin:0;color:#238273;font-weight:800}.finance-hero h2{margin:6px 0;color:#193f42}.finance-hero span{color:#647a7d}.finance-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.finance-metrics article{padding:20px;border:1px solid #e0e9e7;border-radius:15px;background:#fff}.finance-metrics small,.finance-metrics p{color:#748789}.finance-metrics strong{display:block;margin:8px 0;color:#173f42;font-size:25px}.finance-metrics p{margin:0;font-size:12px}.finance-header{display:flex;align-items:center;justify-content:space-between}.finance-header :deep(.el-select){width:150px}@media(max-width:900px){.finance-metrics{grid-template-columns:repeat(2,1fr)}}@media(max-width:560px){.finance-metrics{grid-template-columns:1fr}}
</style>
