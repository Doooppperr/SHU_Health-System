<template>
  <div class="workspace-page agent-ops">
    <section class="welcome-panel welcome-panel--admin">
      <div>
        <p>Agent 安全运营</p>
        <h2>人工工单与外部客户端</h2>
        <span>这里只展示任务摘要和授权元数据，不展示健康档案或 Agent 会话正文。</span>
      </div>
      <span class="admin-shield">AGENT</span>
    </section>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="人工工单" name="tickets">
        <div class="toolbar">
          <el-select v-model="ticketStatus" clearable placeholder="全部状态" @change="loadTickets">
            <el-option label="待处理" value="open" />
            <el-option label="处理中" value="in_progress" />
            <el-option label="已解决" value="resolved" />
            <el-option label="已关闭" value="closed" />
          </el-select>
          <el-button @click="loadTickets">刷新</el-button>
        </div>
        <el-table v-loading="ticketLoading" :data="tickets" empty-text="暂无人工工单">
          <el-table-column prop="created_at" label="创建时间" min-width="170">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column prop="username" label="用户" min-width="110" />
          <el-table-column prop="category" label="类别" min-width="110" />
          <el-table-column prop="priority" label="优先级" min-width="90" />
          <el-table-column prop="summary" label="摘要" min-width="280" show-overflow-tooltip />
          <el-table-column prop="status" label="状态" min-width="110" />
          <el-table-column label="处理" min-width="230" fixed="right">
            <template #default="{ row }">
              <el-button size="small" :disabled="row.status === 'in_progress'" @click="setTicket(row, 'in_progress')">接手</el-button>
              <el-button size="small" type="success" :disabled="row.status === 'resolved'" @click="setTicket(row, 'resolved')">解决</el-button>
              <el-button size="small" :disabled="row.status === 'closed'" @click="setTicket(row, 'closed')">关闭</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="OAuth 客户端" name="oauth">
        <div class="toolbar"><el-button @click="loadClients">刷新</el-button></div>
        <el-table v-loading="clientLoading" :data="clients" empty-text="暂无外部客户端">
          <el-table-column prop="client_name" label="客户端" min-width="150" />
          <el-table-column prop="client_id" label="Client ID" min-width="230" show-overflow-tooltip />
          <el-table-column label="回调地址" min-width="260">
            <template #default="{ row }">{{ row.redirect_uris.join("；") }}</template>
          </el-table-column>
          <el-table-column label="权限范围" min-width="220">
            <template #default="{ row }">{{ row.scopes.join(" ") }}</template>
          </el-table-column>
          <el-table-column prop="status" label="状态" min-width="100" />
          <el-table-column label="审核" min-width="190" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" :disabled="row.status === 'approved'" @click="decideClient(row, 'approve')">批准</el-button>
              <el-button size="small" type="danger" :disabled="row.status === 'revoked'" @click="decideClient(row, row.status === 'pending' ? 'reject' : 'revoke')">
                {{ row.status === "pending" ? "拒绝" : "撤销" }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";

import {
  fetchAdminSupportHandoffs,
  updateAdminSupportHandoff,
} from "../../api/agent";
import { decideOAuthClient, fetchOAuthClients } from "../../api/oauth";

const activeTab = ref("tickets");
const ticketStatus = ref("");
const ticketLoading = ref(false);
const clientLoading = ref(false);
const tickets = ref([]);
const clients = ref([]);

function formatTime(value) {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
}

async function loadTickets() {
  ticketLoading.value = true;
  try {
    const response = await fetchAdminSupportHandoffs(
      ticketStatus.value ? { status: ticketStatus.value } : {},
    );
    tickets.value = response.data.items || [];
  } finally {
    ticketLoading.value = false;
  }
}

async function setTicket(row, status) {
  await updateAdminSupportHandoff(row.id, { status });
  ElMessage.success("工单状态已更新");
  await loadTickets();
}

async function loadClients() {
  clientLoading.value = true;
  try {
    const response = await fetchOAuthClients();
    clients.value = response.data.items || [];
  } finally {
    clientLoading.value = false;
  }
}

async function decideClient(row, decision) {
  await decideOAuthClient(row.client_id, decision);
  ElMessage.success("客户端状态已更新");
  await loadClients();
}

onMounted(() => Promise.all([loadTickets(), loadClients()]));
</script>

<style scoped>
.agent-ops :deep(.el-tabs__content) { overflow: visible; }
.toolbar { display: flex; justify-content: flex-end; gap: 10px; margin: 12px 0; }
.toolbar .el-select { width: 160px; }
</style>
