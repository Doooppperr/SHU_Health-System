<template>
  <div class="workspace-page">
    <section class="page-intro">
      <div>
        <p>用户服务反馈</p>
        <h2>投诉处理</h2>
        <span>及时说明处理措施。机构回复后，由用户确认解决或申请平台介入。</span>
      </div>
      <el-select v-model="status" clearable placeholder="全部状态" style="width: 180px" @change="statusChanged">
        <el-option label="待机构处理" value="institution_pending" />
        <el-option label="待用户确认" value="user_confirmation" />
        <el-option label="待平台处理" value="platform_pending" />
        <el-option label="平台处理中" value="platform_processing" />
        <el-option label="已解决" value="resolved" />
      </el-select>
    </section>

    <el-alert
      title="回复应包含核查结果、已经采取的措施和后续联系方式；请勿在回复中泄露受检者健康数据。"
      type="info"
      show-icon
      :closable="false"
    />
    <section v-loading="loading" class="complaint-grid">
      <article v-for="item in items" :id="`org-complaint-${item.id}`" :key="item.id" class="complaint-work-card">
        <header>
          <div><strong>投诉编号 {{ item.display_id || item.id }}</strong><small>{{ item.created_at }}</small></div>
          <el-tag :type="complaintMeta(item.status).type">{{ complaintMeta(item.status).label }}</el-tag>
        </header>
        <dl>
          <div><dt>受检者</dt><dd>{{ item.appointment?.subject_name || item.complainant?.username || "平台用户" }}</dd></div>
          <div><dt>关联预约</dt><dd>{{ item.appointment?.display_id || item.appointment?.id || item.appointment_id || "—" }}</dd></div>
          <div><dt>投诉类型</dt><dd>{{ item.category_label || item.category || "其他" }}</dd></div>
        </dl>
        <p class="complaint-content">{{ item.content || item.description }}</p>
        <div v-if="item.institution_reply" class="reply-box">
          <strong>机构已回复</strong><p>{{ item.institution_reply }}</p>
        </div>
        <div v-if="item.escalation_reason" class="reply-box is-warning">
          <strong>用户申请平台处理</strong><p>{{ item.escalation_reason }}</p>
        </div>
        <el-timeline v-if="conversationMessages(item).length" class="complaint-message-timeline">
          <el-timeline-item
            v-for="message in conversationMessages(item)"
            :key="message.id || `${message.sender_role}-${message.created_at}`"
            :timestamp="formatTime(message.created_at)"
          >
            <strong>{{ senderLabel(message.sender_role) }}</strong>
            <p>{{ message.content }}</p>
          </el-timeline-item>
        </el-timeline>
        <footer>
          <el-button
            v-if="['institution_pending', 'pending_institution', 'institution_processing'].includes(item.status)"
            type="primary"
            @click="openReply(item)"
          >
            回复并提交处理结果
          </el-button>
          <span v-else>当前阶段无需机构操作</span>
        </footer>
      </article>
      <el-empty v-if="!loading && !items.length" description="当前没有投诉记录" />
    </section>
    <el-pagination
      v-if="pagination.total > pagination.page_size"
      v-model:current-page="pagination.page"
      :page-size="pagination.page_size"
      :total="pagination.total"
      layout="total, prev, pager, next"
      style="justify-content:flex-end;margin-top:16px"
      @current-change="load"
    />

    <el-dialog v-model="dialogVisible" title="回复用户投诉" width="min(560px, 92vw)">
      <el-form label-position="top">
        <el-form-item label="处理结果" required>
          <el-input
            v-model.trim="replyContent"
            type="textarea"
            :rows="6"
            maxlength="1200"
            show-word-limit
            placeholder="请说明核查结论、处理措施与后续安排"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitReply">提交回复</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { nextTick, onMounted, reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { useRoute } from "vue-router";

import { fetchOrgComplaints, replyOrgComplaint } from "../../api/complaints";
import { complaintMeta } from "../../utils/v12";

const loading = ref(false);
const route = useRoute();
const submitting = ref(false);
const dialogVisible = ref(false);
const status = ref([
  "institution_pending",
  "user_confirmation",
  "platform_pending",
  "platform_processing",
  "resolved",
].includes(String(route.query.status)) ? String(route.query.status) : "");
const items = ref([]);
const current = ref(null);
const replyContent = ref("");
const pagination = reactive({ page: 1, page_size: 15, total: 0, pages: 0 });

async function load() {
  loading.value = true;
  try {
    const { data } = await fetchOrgComplaints({
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

async function locateComplaint(complaintId) {
  if (!complaintId) return;
  let matched = items.value.find((item) => Number(item.id) === complaintId);
  let matchedPage = pagination.page;
  for (let page = 1; !matched && page <= pagination.pages; page += 1) {
    if (page === pagination.page) continue;
    let data;
    try {
      ({ data } = await fetchOrgComplaints({
        page,
        page_size: pagination.page_size,
      }));
    } catch (error) {
      ElMessage.error(error?.response?.data?.message || "投诉深链定位失败，请稍后重试");
      return;
    }
    matched = (data.items || []).find((item) => Number(item.id) === complaintId);
    if (matched) {
      matchedPage = page;
      items.value = data.items || [];
      Object.assign(pagination, data.pagination || { page });
    }
  }
  if (!matched) return;
  pagination.page = matchedPage;
  await nextTick();
  document.getElementById(`org-complaint-${complaintId}`)?.scrollIntoView?.({
    behavior: "smooth",
    block: "center",
  });
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
}

async function statusChanged() {
  pagination.page = 1;
  await load();
}

function senderLabel(role) {
  return { user: "用户", institution_admin: "机构", admin: "平台" }[role] || "处理记录";
}

function conversationMessages(item) {
  return (item.messages || []).filter((message, index) => !(
    index === 0 && message.sender_role === "user" && message.content === item.content
  ));
}

function openReply(item) {
  current.value = item;
  replyContent.value = "";
  dialogVisible.value = true;
}

async function submitReply() {
  if (replyContent.value.length < 5) return ElMessage.warning("请填写至少 5 个字符的处理结果");
  submitting.value = true;
  try {
    await replyOrgComplaint(current.value.id, replyContent.value);
    dialogVisible.value = false;
    ElMessage.success("处理结果已发送，等待用户确认");
    await load();
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "投诉回复失败");
  } finally {
    submitting.value = false;
  }
}

onMounted(async () => {
  const complaintId = Number(route.query.complaint_id);
  // A notification can outlive the status it was generated for, so deep links
  // search the complete institution queue instead of applying a stale filter.
  if (complaintId) status.value = "";
  await load();
  await locateComplaint(complaintId);
});

watch(() => route.query.complaint_id, async (value, previous) => {
  const complaintId = Number(value);
  if (!complaintId || value === previous) return;
  status.value = "";
  pagination.page = 1;
  await load();
  await locateComplaint(complaintId);
});
</script>

<style scoped>
.complaint-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:16px;margin-top:18px}
.complaint-work-card{display:grid;gap:14px;padding:18px;border:1px solid var(--el-border-color);border-radius:17px;background:var(--el-bg-color)}
.complaint-work-card header,.complaint-work-card footer{display:flex;align-items:center;justify-content:space-between;gap:12px}
.complaint-work-card header>div{display:grid;gap:3px}.complaint-work-card small,.complaint-work-card footer span{color:var(--el-text-color-secondary)}
.complaint-work-card dl{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:0}.complaint-work-card dl>div{padding:9px;border-radius:10px;background:var(--el-fill-color-light)}
.complaint-work-card dt{color:var(--el-text-color-secondary);font-size:12px}.complaint-work-card dd{margin:4px 0 0;font-weight:700}
  .complaint-content{margin:0;line-height:1.7}.reply-box{padding:12px;border-radius:12px;background:#edf8f5}.reply-box.is-warning{background:#fff5e5}.reply-box p{margin:5px 0 0}
  .complaint-message-timeline{margin:0;padding:12px 12px 0;border-radius:12px;background:var(--el-fill-color-extra-light)}.complaint-message-timeline p{margin:4px 0 0}
@media(max-width:620px){.complaint-work-card dl{grid-template-columns:1fr}}
</style>
