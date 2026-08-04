<template>
  <div class="home-shell">
    <el-card class="home-card">
      <template #header>
        <div class="top-bar">
          <span>评论审核</span>
          <MainNavActions />
        </div>
      </template>

      <el-alert
        v-if="errorMessage"
        :title="errorMessage"
        type="error"
        :closable="false"
        style="margin-bottom: 16px"
      />

      <el-alert
        v-if="forbiddenMessage"
        :title="forbiddenMessage"
        type="warning"
        :closable="false"
        style="margin-bottom: 16px"
      />

      <el-segmented v-if="!forbiddenMessage" v-model="mode" :options="moderationOptions" style="margin-bottom:16px" @change="modeChanged" />

      <el-table v-if="!forbiddenMessage && mode !== 'appeals'" :data="visibleComments" border v-loading="loading" empty-text="当前没有待处理内容">
        <el-table-column label="机构" min-width="220">
          <template #default="scope">
            {{ scope.row.institution?.name }} · {{ scope.row.institution?.branch_name }}
          </template>
        </el-table-column>
        <el-table-column label="用户" min-width="120">
          <template #default="scope">
            {{ scope.row.user?.username || "-" }}
          </template>
        </el-table-column>
        <el-table-column prop="rating" label="评分" width="90" />
        <el-table-column prop="content" label="评论内容" min-width="320" />
        <el-table-column label="展示状态" min-width="190">
          <template #default="{ row }">
            <el-tag :type="row.is_visible ? 'success' : row.hidden_reason ? 'danger' : 'warning'">
              {{ row.is_visible ? "公开展示" : row.hidden_reason ? "已隐藏" : "待审核" }}
            </el-tag>
            <small v-if="row.hidden_reason" class="moderation-reason">原因：{{ row.hidden_reason }}</small>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="提交时间" min-width="180" />
        <el-table-column label="机构回复审核" min-width="300">
          <template #default="scope">
            <template v-if="scope.row.reply">
              <p>{{ scope.row.reply.content }}</p><el-tag :type="scope.row.reply.status==='approved'?'success':scope.row.reply.status==='rejected'?'danger':'warning'">{{ scope.row.reply.status_label }}</el-tag>
              <div v-if="scope.row.reply.status==='pending'" style="margin-top:8px"><el-button link type="success" @click="approveReply(scope.row.reply)">通过回复</el-button><el-button link type="danger" @click="rejectReply(scope.row.reply)">驳回回复</el-button></div>
              <small v-if="scope.row.reply.review_note">原因：{{ scope.row.reply.review_note }}</small>
            </template><span v-else>尚未回复</span>
          </template>
        </el-table-column>
        <el-table-column label="可见" width="110">
          <template #default="scope">
            <el-switch
              :model-value="scope.row.is_visible"
              :active-value="true"
              :inactive-value="false"
              @change="(value) => toggleVisibility(scope.row, value)"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="scope">
            <el-button type="warning" link :disabled="!scope.row.user?.id" @click="openModeration(scope.row)">审核处理</el-button>
          </template>
        </el-table-column>
      </el-table>

      <template v-else-if="!forbiddenMessage">
        <div class="appeal-toolbar">
          <span>申诉状态</span>
          <el-select v-model="appealStatus" style="width: 160px" @change="appealStatusChanged">
            <el-option
              v-for="option in appealStatusOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </div>
        <el-table :data="appeals" border v-loading="appealLoading" empty-text="当前没有封禁申诉">
          <el-table-column label="用户" min-width="150">
            <template #default="{ row }">
              {{ row.user?.username || row.user?.display_name || row.sanction?.user?.username || "平台用户" }}
            </template>
          </el-table-column>
          <el-table-column label="封禁信息" min-width="260">
            <template #default="{ row }">
              <span>{{ row.sanction?.reason || row.sanction_reason || "违规发言" }}</span>
              <small class="moderation-reason">期限：{{ row.sanction?.duration_label || "永久" }}</small>
            </template>
          </el-table-column>
          <el-table-column prop="content" label="申诉说明" min-width="320" />
          <el-table-column label="状态" width="110"><template #default="{ row }"><el-tag :type="row.status === 'pending' ? 'warning' : row.status === 'approved' ? 'success' : 'info'">{{ appealStatusLabel(row.status) }}</el-tag></template></el-table-column>
          <el-table-column label="操作" width="190" fixed="right">
            <template #default="{ row }">
              <template v-if="row.status === 'pending'">
                <el-button link type="success" @click="resolveAppeal(row, 'unban')">解封用户</el-button>
                <el-button link type="danger" @click="resolveAppeal(row, 'reject')">驳回申诉</el-button>
              </template>
              <span v-else>{{ row.review_note || row.resolution_reason || "已处理" }}</span>
            </template>
          </el-table-column>
        </el-table>
      </template>
      <el-pagination v-if="!forbiddenMessage && mode !== 'appeals' && pagination.total>pagination.page_size" v-model:current-page="pagination.page" :page-size="pagination.page_size" :total="pagination.total" layout="total, prev, pager, next" style="margin-top:16px;justify-content:flex-end" @current-change="loadComments"/>
      <el-pagination v-if="!forbiddenMessage && mode === 'appeals' && appealPagination.total>appealPagination.page_size" v-model:current-page="appealPagination.page" :page-size="appealPagination.page_size" :total="appealPagination.total" layout="total, prev, pager, next" style="margin-top:16px;justify-content:flex-end" @current-change="loadAppeals"/>

      <el-dialog v-model="moderationVisible" title="处理用户评论" width="min(560px, 92vw)" append-to-body>
        <el-alert
          title="评论原文会保留。解封用户不会自动恢复已隐藏评论。"
          type="info"
          show-icon
          :closable="false"
          style="margin-bottom: 16px"
        />
        <el-form label-position="top">
          <el-form-item label="处理方式" required>
            <el-select v-model="moderationForm.action" style="width:100%">
              <el-option label="仅隐藏评论" value="hide" />
              <el-option label="隐藏并禁言 7 天" value="ban_7" />
              <el-option label="隐藏并禁言 30 天" value="ban_30" />
              <el-option label="隐藏并永久禁言" value="ban_permanent" />
            </el-select>
          </el-form-item>
          <el-form-item label="处理原因" required>
            <el-input
              v-model.trim="moderationForm.reason"
              type="textarea"
              :rows="5"
              maxlength="500"
              show-word-limit
              placeholder="例如：恶意言论、重复灌水或虚假内容"
            />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="moderationVisible = false">取消</el-button>
          <el-button type="warning" :loading="moderationSubmitting" @click="submitModeration">确认处理</el-button>
        </template>
      </el-dialog>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import MainNavActions from "../components/MainNavActions.vue";
import { approveCommentReply, fetchCommentAppeals, fetchCommentModerationList, rejectCommentReply, resolveCommentAppeal, sanctionCommentUser, updateCommentVisibility } from "../api/comments";

const loading = ref(false);
const comments = ref([]);
const appeals = ref([]);
const appealLoading = ref(false);
const errorMessage = ref("");
const forbiddenMessage = ref("");
const mode = ref("comments");
const moderationVisible = ref(false);
const moderationSubmitting = ref(false);
const moderationForm = reactive({ row: null, action: "hide", reason: "" });
const pagination = reactive({ page: 1, page_size: 15, total: 0, pages: 0 });
const appealPagination = reactive({ page: 1, page_size: 15, total: 0, pages: 0 });
const moderationCounts = reactive({ comments_pending: 0, replies_pending: 0, all: 0 });
const appealCounts = reactive({ pending: 0, approved: 0, rejected: 0, all: 0 });
const appealStatus = ref("pending");
const appealStatusOptions = computed(() => [
  { label: `待处理（${appealCounts.pending}）`, value: "pending" },
  { label: `全部（${appealCounts.all}）`, value: "all" },
  { label: `已解封（${appealCounts.approved}）`, value: "approved" },
  { label: `维持封禁（${appealCounts.rejected}）`, value: "rejected" },
]);
const moderationOptions = computed(() => [
  { label: `用户评价待审核（${moderationCounts.comments_pending}）`, value: "comments" },
  { label: `机构回复待审核（${moderationCounts.replies_pending}）`, value: "replies" },
  { label: `封禁申诉（${appealCounts.pending}）`, value: "appeals" },
  { label: `全部审核记录（${moderationCounts.all}）`, value: "all" },
]);
const visibleComments = computed(() => comments.value);

const loadComments = async () => {
  loading.value = true;
  errorMessage.value = "";
  forbiddenMessage.value = "";

  try {
    const queue = ["comments", "replies", "all"].includes(mode.value) ? mode.value : "all";
    const { data } = await fetchCommentModerationList({
      page: pagination.page,
      page_size: pagination.page_size,
      queue,
    });
    comments.value = data.items || [];
    Object.assign(pagination, data.pagination || {});
    Object.assign(moderationCounts, data.counts || {});
  } catch (error) {
    if (error?.response?.status === 403) {
      forbiddenMessage.value = "仅管理员可以访问评论审核。";
      comments.value = [];
    } else {
      errorMessage.value = error?.response?.data?.message || "评论审核数据加载失败";
    }
  } finally {
    loading.value = false;
  }
};

const toggleVisibility = async (row, isVisible) => {
  if (!isVisible) {
    openModeration(row);
    return;
  }
  try {
    await updateCommentVisibility(row.id, { is_visible: true });
    row.is_visible = isVisible;
    ElMessage.success("评论已恢复显示");
    pagination.page = 1;
    await loadComments();
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "可见性更新失败");
    await loadComments();
  }
};

function openModeration(row) {
  moderationForm.row = row;
  moderationForm.action = "hide";
  moderationForm.reason = row.hidden_reason || "";
  moderationVisible.value = true;
}

async function submitModeration() {
  const row = moderationForm.row;
  if (!row?.id || !moderationForm.reason) return ElMessage.warning("请填写处理原因");
  moderationSubmitting.value = true;
  try {
    if (moderationForm.action === "hide") {
      await updateCommentVisibility(row.id, {
        is_visible: false,
        reason: moderationForm.reason,
      });
    } else {
      const durationDays = moderationForm.action === "ban_7"
        ? 7
        : moderationForm.action === "ban_30"
          ? 30
          : null;
      await sanctionCommentUser(row.user.id, moderationForm.reason, row.id, durationDays);
    }
    moderationVisible.value = false;
    ElMessage.success(moderationForm.action === "hide" ? "评论已隐藏并保留原文" : "评论已隐藏，禁言已生效并通知用户");
    pagination.page = 1;
    await loadComments();
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "评论处理失败");
  } finally {
    moderationSubmitting.value = false;
  }
}

const approveReply = async (reply) => {
  try {
    await approveCommentReply(reply.id);
    ElMessage.success("机构回复已审核通过");
    pagination.page = 1;
    await loadComments();
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "审核操作失败");
  }
};
const rejectReply = async (reply) => {
  try {
    const note = await ElMessageBox.prompt("请填写具体、可修改的驳回原因", "驳回机构回复", {
      confirmButtonText: "确认驳回",
      cancelButtonText: "取消",
      inputValidator: (value) => Boolean(value?.trim()) || "请填写驳回原因",
    });
    await rejectCommentReply(reply.id, note.value.trim());
    ElMessage.success("机构回复已驳回");
    pagination.page = 1;
    await loadComments();
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(error?.response?.data?.message || "审核操作失败");
    }
  }
};

async function loadAppeals() {
  appealLoading.value = true;
  errorMessage.value = "";
  try {
    const params = {
      page: appealPagination.page,
      page_size: appealPagination.page_size,
    };
    if (appealStatus.value !== "all") params.status = appealStatus.value;
    const { data } = await fetchCommentAppeals(params);
    appeals.value = data.items || [];
    Object.assign(appealPagination, data.pagination || {});
    Object.assign(appealCounts, data.counts || {});
  } catch (error) {
    if (error?.response?.status === 403) {
      forbiddenMessage.value = "仅管理员可以访问评论审核。";
      appeals.value = [];
    } else {
      errorMessage.value = error?.response?.data?.message || "封禁申诉加载失败";
    }
  } finally {
    appealLoading.value = false;
  }
}

async function modeChanged(value) {
  mode.value = value;
  if (value === "appeals") {
    appealPagination.page = 1;
    await loadAppeals();
    return;
  }
  pagination.page = 1;
  await loadComments();
}

async function appealStatusChanged() {
  appealPagination.page = 1;
  await loadAppeals();
}

function appealStatusLabel(status) {
  return { pending: "待处理", approved: "已解封", unbanned: "已解封", rejected: "维持封禁" }[status] || "已处理";
}

async function resolveAppeal(row, action) {
  try {
    const title = action === "unban" ? "解封用户" : "驳回申诉";
    const { value } = await ElMessageBox.prompt("请填写处理说明，该说明会通知用户。", title, {
      inputType: "textarea",
      confirmButtonText: "确认处理",
      cancelButtonText: "取消",
      inputPattern: /.+/,
      inputErrorMessage: "处理说明不能为空",
    });
    await resolveCommentAppeal(row.id, action, value.trim());
    ElMessage.success(action === "unban" ? "用户已解封" : "申诉已驳回，继续保持封禁");
    appealPagination.page = 1;
    await loadAppeals();
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(error?.response?.data?.message || "申诉处理失败");
    }
  }
}

onMounted(async () => {
  await loadComments();
  await loadAppeals();
});
</script>

<style scoped>
.moderation-reason {
  display: block;
  margin-top: 6px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}

.appeal-toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  margin-bottom: 12px;
  color: var(--el-text-color-secondary);
}
</style>
