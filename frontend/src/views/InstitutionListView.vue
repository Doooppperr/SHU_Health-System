<template>
  <div class="home-shell">
    <el-card class="home-card">
      <template #header>
        <div class="top-bar">
          <span>机构列表</span>
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

      <el-skeleton :rows="4" animated v-if="loading" />

      <el-empty v-else-if="institutions.length === 0" description="暂无机构数据" />

      <el-row v-else :gutter="16">
        <el-col v-for="institution in institutions" :key="institution.id" :xs="24" :sm="12" :lg="8" style="margin-bottom: 16px">
          <el-card shadow="hover" class="institution-card">
            <InstitutionCoverImage :institution="institution" />
            <h3 class="institution-name">{{ institution.name }} · {{ institution.branch_name }}</h3>
            <p class="institution-meta">区域：{{ institution.district }}</p>
            <p class="institution-meta">地址：{{ institution.address }}</p>
            <p class="institution-meta">电话：{{ institution.consult_phone || '-' }}</p>
            <p class="institution-meta">轮休日：{{ institution.closed_day || '-' }}</p>
            <p class="institution-meta">套餐数量：{{ institution.package_count }}</p>
            <el-button type="primary" @click="goDetail(institution.id)">查看详情与套餐</el-button>
          </el-card>
        </el-col>
      </el-row>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import MainNavActions from "../components/MainNavActions.vue";
import InstitutionCoverImage from "../components/InstitutionCoverImage.vue";
import { fetchInstitutions } from "../api/institutions";
import { useAuthStore } from "../stores/auth";

const router = useRouter();
const authStore = useAuthStore();

const loading = ref(false);
const institutions = ref([]);
const errorMessage = ref("");

const loadInstitutions = async () => {
  loading.value = true;
  errorMessage.value = "";

  try {
    const { data } = await fetchInstitutions();
    institutions.value = data.items || [];
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "机构列表加载失败";
  } finally {
    loading.value = false;
  }
};

const goDetail = (id) => {
  router.push({ name: "institution-detail", params: { id } });
};

const goRecords = () => {
  router.push({ name: "records" });
};

const goTrends = () => {
  router.push({ name: "trends" });
};

const goFriends = () => {
  router.push({ name: "friends" });
};

const goCommentModeration = () => {
  router.push({ name: "comment-moderation" });
};

const goProfile = () => {
  router.push({ name: "profile" });
};

const logout = () => {
  authStore.logout();
  router.push({ name: "login" });
};

onMounted(() => {
  loadInstitutions();
});
</script>
