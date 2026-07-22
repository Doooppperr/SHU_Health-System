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

      <el-segmented v-if="!forbiddenMessage" v-model="mode" :options="moderationOptions" style="margin-bottom:16px" />

      <el-table v-if="!forbiddenMessage" :data="visibleComments" border v-loading="loading" empty-text="当前没有待处理内容">
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
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="scope">
            <el-button type="danger" link @click="removeComment(scope.row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import MainNavActions from "../components/MainNavActions.vue";
import { approveCommentReply, deleteComment, fetchCommentModerationList, rejectCommentReply, updateCommentVisibility } from "../api/comments";

const loading = ref(false);
const comments = ref([]);
const errorMessage = ref("");
const forbiddenMessage = ref("");
const mode = ref("comments");
const moderationOptions = computed(() => [
  { label: `用户评价待审核（${comments.value.filter((item)=>!item.is_visible).length}）`, value: "comments" },
  { label: `机构回复待审核（${comments.value.filter((item)=>item.reply?.status==="pending").length}）`, value: "replies" },
  { label: "全部审核记录", value: "all" },
]);
const visibleComments = computed(() => mode.value === "comments" ? comments.value.filter((item)=>!item.is_visible) : mode.value === "replies" ? comments.value.filter((item)=>item.reply?.status==="pending") : comments.value);

const loadComments = async () => {
  loading.value = true;
  errorMessage.value = "";
  forbiddenMessage.value = "";

  try {
    const { data } = await fetchCommentModerationList();
    comments.value = data.items || [];
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
  try {
    await updateCommentVisibility(row.id, { is_visible: isVisible });
    row.is_visible = isVisible;
    ElMessage.success(isVisible ? "评论已显示" : "评论已隐藏");
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "可见性更新失败");
    await loadComments();
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

const approveReply = async (reply) => { try { await approveCommentReply(reply.id); ElMessage.success("机构回复已审核通过"); await loadComments(); } catch (error) { ElMessage.error(error?.response?.data?.message || "审核操作失败"); } };
const rejectReply = async (reply) => { try { const note = await ElMessageBox.prompt("请填写具体、可修改的驳回原因", "驳回机构回复", { confirmButtonText:"确认驳回", cancelButtonText:"取消", inputValidator:(value)=>Boolean(value?.trim())||"请填写驳回原因" }); await rejectCommentReply(reply.id,note.value.trim()); ElMessage.success("机构回复已驳回"); await loadComments(); } catch(error) { if(error!=="cancel"&&error!=="close") ElMessage.error(error?.response?.data?.message||"审核操作失败"); } };

onMounted(async () => {
  await loadComments();
});
</script>
