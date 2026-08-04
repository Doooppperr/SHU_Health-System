<template>
  <div class="workspace-page">
    <section class="page-intro">
      <div>
        <p>服务质量治理</p>
        <h2>投诉与退款裁决</h2>
        <span>查看机构处理进度；用户升级后由平台接手、回复并关闭投诉。</span>
      </div>
      <div class="admin-complaint-filter">
        <el-select v-model="status" clearable placeholder="全部状态" @change="statusChanged">
          <el-option label="待机构处理" value="institution_pending" />
          <el-option label="待用户确认" value="user_confirmation" />
          <el-option label="待平台处理" value="platform_pending" />
          <el-option label="平台处理中" value="platform_processing" />
          <el-option label="已解决" value="resolved" />
        </el-select>
        <el-button @click="load">刷新</el-button>
      </div>
    </section>

    <el-table v-loading="loading" :data="items" border empty-text="暂无投诉记录">
      <el-table-column label="投诉信息" min-width="250">
        <template #default="{ row }">
          <strong>{{ row.display_id || `#${row.id}` }}</strong>
          <p class="table-detail">{{ row.category_label || row.category }} · {{ row.created_at }}</p>
        </template>
      </el-table-column>
      <el-table-column label="用户 / 机构" min-width="220">
        <template #default="{ row }">
          <span>{{ row.appointment?.subject_name || row.complainant?.username || row.user?.display_name || row.subject_name || "平台用户" }}</span>
          <p class="table-detail">{{ row.institution?.name || row.institution_name || "—" }}</p>
        </template>
      </el-table-column>
      <el-table-column label="内容" min-width="300">
        <template #default="{ row }"><span class="complaint-summary">{{ row.content || row.description }}</span></template>
      </el-table-column>
      <el-table-column label="状态" width="140">
        <template #default="{ row }"><el-tag :type="complaintMeta(row.status).type">{{ complaintMeta(row.status).label }}</el-tag></template>
      </el-table-column>
      <el-table-column label="操作" width="250" fixed="right">
        <template #default="{ row }">
          <el-button link @click="showDetail(row)">详情</el-button>
          <el-button v-if="['platform_pending', 'escalated'].includes(row.status)" link type="primary" @click="start(row)">接手处理</el-button>
          <el-button v-if="['platform_processing', 'admin_processing'].includes(row.status)" link type="primary" @click="openReply(row)">平台回复</el-button>
          <el-button v-if="['platform_processing', 'admin_processing'].includes(row.status)" link type="success" @click="resolve(row)">关闭投诉</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-drawer v-model="detailVisible" title="投诉详情" size="min(620px, 94vw)">
      <template v-if="current">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="用户">{{ current.appointment?.subject_name || current.complainant?.username || current.user?.display_name || current.subject_name || "平台用户" }}</el-descriptions-item>
          <el-descriptions-item label="机构">{{ current.institution?.name || current.institution_name }}</el-descriptions-item>
          <el-descriptions-item label="预约">{{ current.appointment?.display_id || current.appointment?.id || current.appointment_id }}</el-descriptions-item>
          <el-descriptions-item label="投诉内容">{{ current.content || current.description }}</el-descriptions-item>
          <el-descriptions-item label="机构回复">{{ current.institution_reply || "尚未回复" }}</el-descriptions-item>
          <el-descriptions-item label="升级原因">{{ current.escalation_reason || "未申请平台介入" }}</el-descriptions-item>
          <el-descriptions-item label="平台回复">{{ current.admin_reply || "尚未回复" }}</el-descriptions-item>
          <el-descriptions-item v-if="current.refund" label="退款申请">¥ {{ Number(current.refund.amount || 0).toFixed(2) }} · {{ current.refund.status }}</el-descriptions-item>
        </el-descriptions>
        <el-timeline v-if="(current.events || current.timeline)?.length" style="margin-top: 20px">
          <el-timeline-item v-for="event in current.events || current.timeline" :key="event.id || event.created_at" :timestamp="event.created_at">
            <strong>{{ event.title || eventLabel(event.event_type || event.type) }}</strong><p>{{ event.content }}</p>
          </el-timeline-item>
        </el-timeline>
      </template>
    </el-drawer>

    <el-dialog v-model="replyVisible" title="平台回复用户" width="min(560px, 92vw)">
      <el-input v-model.trim="replyContent" type="textarea" :rows="6" maxlength="1200" show-word-limit placeholder="说明平台核查结果和处理措施" />
      <template #footer>
        <el-button @click="replyVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitReply">发送回复</el-button>
      </template>
    </el-dialog>
    <el-pagination
      v-if="pagination.total > pagination.page_size"
      v-model:current-page="pagination.page"
      :page-size="pagination.page_size"
      :total="pagination.total"
      layout="total, prev, pager, next"
      style="justify-content:flex-end;margin-top:16px"
      @current-change="load"
    />
  </div>
</template>

<script setup>
import { onMounted, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useRoute } from "vue-router";

import {
  fetchAdminComplaints,
  replyAdminComplaint,
  resolveAdminComplaint,
  startAdminComplaint,
} from "../../api/complaints";
import { complaintMeta } from "../../utils/v12";

const loading = ref(false);
const route = useRoute();
const submitting = ref(false);
const status = ref("");
const items = ref([]);
const current = ref(null);
const detailVisible = ref(false);
const replyVisible = ref(false);
const replyContent = ref("");
const pagination = reactive({ page: 1, page_size: 15, total: 0, pages: 0 });

async function load() {
  loading.value = true;
  try {
    const { data } = await fetchAdminComplaints({
      status: status.value || undefined,
      page: pagination.page,
      page_size: pagination.page_size,
    });
    items.value = data.items || [];
    Object.assign(pagination, data.pagination || {});
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "投诉记录加载失败");
  } finally {
    loading.value = false;
  }
}

async function statusChanged() {
  pagination.page = 1;
  await load();
}

async function locateComplaint(complaintId) {
  if (!complaintId) return;
  let requested = items.value.find((item) => Number(item.id) === complaintId);
  for (let page = 1; !requested && page <= pagination.pages; page += 1) {
    if (page === pagination.page) continue;
    let data;
    try {
      ({ data } = await fetchAdminComplaints({
        page,
        page_size: pagination.page_size,
      }));
    } catch (error) {
      ElMessage.error(error?.response?.data?.message || "投诉深链定位失败，请稍后重试");
      return;
    }
    requested = (data.items || []).find(
      (item) => Number(item.id) === complaintId,
    );
    if (requested) {
      items.value = data.items || [];
      Object.assign(pagination, data.pagination || { page });
    }
  }
  if (requested) showDetail(requested);
}

function eventLabel(value) {
  return {
    created: "用户提交投诉",
    institution_replied: "机构回复并请求用户确认",
    user_confirmed: "用户确认解决",
    escalated: "用户申请平台介入",
    admin_started: "平台开始处理",
    admin_replied: "平台回复用户",
    admin_resolved: "平台关闭投诉",
  }[value] || "投诉状态更新";
}

function showDetail(row) {
  current.value = row;
  detailVisible.value = true;
}

async function start(row) {
  try {
    await startAdminComplaint(row.id);
    ElMessage.success("平台已接手该投诉");
    await load();
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "接手处理失败");
  }
}

function openReply(row) {
  current.value = row;
  replyContent.value = "";
  replyVisible.value = true;
}

async function submitReply() {
  if (replyContent.value.length < 5) return ElMessage.warning("请填写完整的平台回复");
  submitting.value = true;
  try {
    await replyAdminComplaint(current.value.id, replyContent.value);
    replyVisible.value = false;
    ElMessage.success("平台回复已发送");
    await load();
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "回复失败");
  } finally {
    submitting.value = false;
  }
}

async function resolve(row) {
  try {
    if (row.admin_reply) {
      await ElMessageBox.confirm(
        "将使用已发送的平台回复作为最终处理结论，并关闭该投诉。",
        "关闭投诉",
        {
          type: "warning",
          confirmButtonText: "标记已解决",
          cancelButtonText: "取消",
        },
      );
    } else {
      const { value } = await ElMessageBox.prompt("请输入最终处理结论", "回复并关闭投诉", {
        inputType: "textarea",
        confirmButtonText: "回复并标记已解决",
        cancelButtonText: "取消",
        inputPattern: /.+/,
        inputErrorMessage: "请填写处理结论",
      });
      await replyAdminComplaint(row.id, value.trim());
    }
    let decision = "no_refund";
    if (row.refund) {
      try {
        await ElMessageBox.confirm(
          "请选择最终责任认定。认定机构责任后，平台将立即退款或要求机构在三天内退款。",
          "退款裁决",
          { distinguishCancelAndClose: true, type: "warning", confirmButtonText: "机构责任并退款", cancelButtonText: "不支持退款" },
        );
        decision = "institution_fault_refund";
      } catch (choice) {
        if (choice === "cancel") decision = "no_refund";
        else throw choice;
      }
    }
    await resolveAdminComplaint(row.id, { decision, decision_note: row.admin_reply || "平台已完成核查" });
    ElMessage.success(decision === "institution_fault_refund" ? "责任已认定，退款流程已启动" : "投诉已关闭，本次不支持退款");
    await load();
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(error?.response?.data?.message || "关闭投诉失败");
    }
  }
}

onMounted(async () => {
  const requestedId = Number(route.query.complaint_id);
  if (requestedId) status.value = "";
  await load();
  await locateComplaint(requestedId);
});

watch(() => route.query.complaint_id, async (value, previous) => {
  const requestedId = Number(value);
  if (!requestedId || value === previous) return;
  status.value = "";
  pagination.page = 1;
  await load();
  await locateComplaint(requestedId);
});
</script>

<style scoped>
.admin-complaint-filter{display:flex;gap:8px}.admin-complaint-filter :deep(.el-select){width:170px}
.table-detail{margin:4px 0 0;color:var(--el-text-color-secondary);font-size:12px}.complaint-summary{display:-webkit-box;overflow:hidden;-webkit-line-clamp:2;-webkit-box-orient:vertical;line-height:1.55}
</style>
