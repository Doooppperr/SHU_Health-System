<template>
  <div class="home-shell">
    <el-card class="home-card">
      <template #header>
        <div class="top-bar">
          <span>机构详情与套餐</span>
          <MainNavActions>
            <template #prefix>
              <el-button plain @click="goBack">返回列表</el-button>
            </template>
          </MainNavActions>
        </div>
      </template>

      <el-alert
        v-if="errorMessage"
        :title="errorMessage"
        type="error"
        :closable="false"
        style="margin-bottom: 16px"
      />

      <el-skeleton :rows="5" animated v-if="loading" />

      <template v-else>
        <InstitutionCoverImage v-if="institution" :institution="institution" variant="detail" />

        <el-descriptions :column="1" border style="margin-bottom: 16px">
          <el-descriptions-item label="机构名称">
            {{ institution?.name || '-' }} · {{ institution?.branch_name || '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="所属区域">{{ institution?.district || '-' }}</el-descriptions-item>
          <el-descriptions-item label="地址">{{ institution?.address || '-' }}</el-descriptions-item>
          <el-descriptions-item label="地铁">{{ institution?.metro_info || '-' }}</el-descriptions-item>
          <el-descriptions-item label="咨询电话">{{ institution?.consult_phone || '-' }}</el-descriptions-item>
          <el-descriptions-item label="轮休日">{{ institution?.closed_day || '-' }}</el-descriptions-item>
          <el-descriptions-item label="简介">{{ institution?.description || '-' }}</el-descriptions-item>
        </el-descriptions>

        <el-table :data="packages" border>
          <el-table-column prop="name" label="套餐名称" min-width="180" />
          <el-table-column prop="focus_area" label="侧重点" min-width="140" />
          <el-table-column prop="gender_scope" label="适用人群" width="120" />
          <el-table-column prop="price" label="价格(元)" width="120" />
          <el-table-column prop="description" label="说明" min-width="260" />
        </el-table>

        <el-card shadow="never" style="margin-top: 16px">
          <template #header>
            <span>机构评论</span>
          </template>

          <div class="comment-form-grid">
            <el-rate v-model="commentForm.rating" :max="5" />
            <el-input
              v-model="commentForm.content"
              type="textarea"
              :rows="3"
              maxlength="1000"
              show-word-limit
              placeholder="请输入评论内容（需有该机构已匹配报告）"
            />
            <el-button type="primary" :loading="commentSubmitting" @click="submitComment">提交评论</el-button>
          </div>

          <el-divider />

          <el-table :data="comments" border empty-text="暂无公开评论">
            <el-table-column label="用户" width="120">
              <template #default="scope">
                {{ scope.row.user?.username || "-" }}
              </template>
            </el-table-column>
            <el-table-column label="评分" width="120">
              <template #default="scope">
                <el-rate :model-value="scope.row.rating" disabled />
              </template>
            </el-table-column>
            <el-table-column prop="content" label="评论内容" min-width="260" />
            <el-table-column prop="created_at" label="时间" min-width="180" />
          </el-table>
        </el-card>
      </template>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { useRoute, useRouter } from "vue-router";

import MainNavActions from "../components/MainNavActions.vue";
import InstitutionCoverImage from "../components/InstitutionCoverImage.vue";
import { createInstitutionComment, fetchInstitutionComments } from "../api/comments";
import { fetchInstitutionDetail, fetchInstitutionPackages } from "../api/institutions";

const route = useRoute();
const router = useRouter();

const loading = ref(false);
const errorMessage = ref("");
const institution = ref(null);
const packages = ref([]);
const comments = ref([]);
const commentSubmitting = ref(false);
const commentForm = reactive({
  rating: 5,
  content: "",
});

const loadData = async () => {
  loading.value = true;
  errorMessage.value = "";

  try {
    const institutionId = route.params.id;
    const [detailResponse, packageResponse] = await Promise.all([
      fetchInstitutionDetail(institutionId),
      fetchInstitutionPackages(institutionId),
    ]);

    institution.value = detailResponse.data.item;
    packages.value = packageResponse.data.items || [];
    await loadComments();
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "机构详情加载失败";
    institution.value = null;
    packages.value = [];
    comments.value = [];
  } finally {
    loading.value = false;
  }
};

const loadComments = async () => {
  const institutionId = route.params.id;
  const { data } = await fetchInstitutionComments(institutionId);
  comments.value = data.items || [];
};

const submitComment = async () => {
  if (!commentForm.content.trim()) {
    ElMessage.error("请输入评论内容");
    return;
  }

  commentSubmitting.value = true;

  try {
    await createInstitutionComment({
      institution_id: Number(route.params.id),
      content: commentForm.content.trim(),
      rating: commentForm.rating,
    });
    commentForm.content = "";
    commentForm.rating = 5;
    ElMessage.success("评论已提交，等待管理员审核展示");
  } catch (error) {
    const errorCode = error?.response?.data?.code;
    const backendMessage = error?.response?.data?.message || "";
    if (
      errorCode === "comment_requires_record"
      || backendMessage === "upload a record for this institution before commenting"
    ) {
      ElMessage.error("请先取得该机构已匹配的体检报告，再提交评论");
      return;
    }
    ElMessage.error(backendMessage || "评论提交失败");
  } finally {
    commentSubmitting.value = false;
  }
};

const goBack = () => {
  router.push({ name: "institutions" });
};


watch(
  () => route.params.id,
  () => {
    loadData();
  }
);

onMounted(() => {
  loadData();
});
</script>
