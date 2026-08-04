<template>
  <div class="home-shell">
    <el-card class="home-card">
      <template #header>
        <div class="top-bar">
          <span>我的评论</span>
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
        v-if="sanction?.status === 'active' || sanction?.is_active || sanction?.active"
        type="error"
        show-icon
        :closable="false"
        style="margin-bottom: 16px"
      >
        <template #title>你的评论权限已被平台封禁</template>
        <p>原因：{{ sanction.reason || "违反平台评论规范" }}</p>
        <p>期限：{{ sanction.duration_label || (sanction.expires_at ? `截至 ${sanction.expires_at}` : "永久") }}</p>
        <p v-if="sanction.appeal?.status === 'pending'">申诉已提交，平台管理员正在处理。</p>
        <p v-else-if="sanction.appeal?.status === 'rejected'">最近申诉已驳回：{{ sanction.appeal.review_note || sanction.appeal.resolution_reason || "继续保持封禁" }}</p>
        <el-button v-if="!sanction.appeal" type="primary" link @click="appealVisible = true">提交一次申诉</el-button>
      </el-alert>

      <el-table :data="comments" border v-loading="loading" empty-text="暂无评论记录">
        <el-table-column label="机构" min-width="220">
          <template #default="scope">
            {{ scope.row.institution?.name }} · {{ scope.row.institution?.branch_name }}
          </template>
        </el-table-column>
        <el-table-column label="评分" width="110">
          <template #default="scope">
            <el-rate :model-value="scope.row.rating" disabled />
          </template>
        </el-table-column>
        <el-table-column prop="content" label="评论内容" min-width="320" />
        <el-table-column label="状态" width="110">
          <template #default="scope">
            <el-tag :type="scope.row.is_visible ? 'success' : 'warning'">
              {{ scope.row.is_visible ? "已公开" : scope.row.hidden_reason ? "已隐藏" : "待审核" }}
            </el-tag>
            <small v-if="scope.row.hidden_reason" class="comment-hidden-reason">{{ scope.row.hidden_reason }}</small>
          </template>
        </el-table-column>
        <el-table-column label="机构回复" min-width="320">
          <template #default="scope">
            <template v-if="scope.row.reply">
              <el-tag v-if="scope.row.reply.is_unread" type="danger" effect="dark">收到机构新回复</el-tag>
              <p style="margin:8px 0 0"><strong>机构回复：</strong>{{ scope.row.reply.content }}</p>
            </template><span v-else>暂无回复</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="提交时间" min-width="180" />
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="scope">
            <el-button type="danger" link @click="removeComment(scope.row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-if="pagination.total>pagination.page_size" v-model:current-page="pagination.page" :page-size="pagination.page_size" :total="pagination.total" layout="total, prev, pager, next" style="margin-top:16px;justify-content:flex-end" @current-change="loadComments"/>
    </el-card>

    <el-dialog v-model="appealVisible" title="评论封禁申诉" width="min(540px, 92vw)">
      <el-alert :title="`当前封禁原因：${sanction?.reason || '违反平台评论规范'}`" type="warning" :closable="false" style="margin-bottom:16px" />
      <el-input v-model.trim="appealContent" type="textarea" :rows="6" maxlength="1000" show-word-limit placeholder="请说明申诉理由和需要平台复核的情况" />
      <template #footer>
        <el-button @click="appealVisible = false">取消</el-button>
        <el-button type="primary" :loading="appealSubmitting" @click="submitAppeal">提交申诉</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import MainNavActions from "../components/MainNavActions.vue";
import { deleteComment, fetchMyComments, fetchMyCommentSanction, markCommentRepliesRead, submitCommentAppeal } from "../api/comments";

const loading = ref(false);
const comments = ref([]);
const errorMessage = ref("");
const sanction = ref(null);
const appealVisible = ref(false);
const appealContent = ref("");
const appealSubmitting = ref(false);
const pagination = reactive({ page: 1, page_size: 15, total: 0, pages: 0 });

const loadComments = async () => {
  loading.value = true;
  errorMessage.value = "";

  try {
    const { data } = await fetchMyComments({ page: pagination.page, page_size: 15 });
    comments.value = data.items || [];
    Object.assign(pagination, data.pagination || {});
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "我的评论加载失败";
  } finally {
    loading.value = false;
  }
};

const removeComment = async (row) => {
  try {
    await ElMessageBox.confirm("删除后不可恢复，确认删除该评论？", "提示", {
      type: "warning",
      confirmButtonText: "确认删除",
      cancelButtonText: "取消",
    });
    await deleteComment(row.id);
    ElMessage.success("评论已删除");
    await loadComments();
  } catch (error) {
    if (error === "cancel") {
      return;
    }
    ElMessage.error(error?.response?.data?.message || "评论删除失败");
  }
};

async function loadSanction() {
  try {
    const { data } = await fetchMyCommentSanction();
    sanction.value = data.item || data.sanction || data || null;
  } catch (error) {
    if (error?.response?.status !== 404) {
      errorMessage.value = error?.response?.data?.message || "评论权限状态加载失败";
    }
  }
}

async function submitAppeal() {
  if (appealContent.value.length < 5) return ElMessage.warning("请填写至少 5 个字符的申诉理由");
  appealSubmitting.value = true;
  try {
    await submitCommentAppeal(sanction.value.id, appealContent.value);
    appealVisible.value = false;
    appealContent.value = "";
    ElMessage.success("申诉已提交，请等待平台管理员处理");
    await loadSanction();
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "申诉提交失败");
  } finally {
    appealSubmitting.value = false;
  }
}

onMounted(async () => {
  await Promise.all([loadComments(), loadSanction()]);
  if (comments.value.some((item) => item.reply?.is_unread)) {
    await markCommentRepliesRead().catch(() => {});
    window.dispatchEvent(new CustomEvent("healthdoc-comment-replies-read"));
  }
});
</script>

<style scoped>
.comment-hidden-reason {
  display: block;
  margin-top: 5px;
  color: var(--el-text-color-secondary);
  line-height: 1.45;
}
</style>
