<template>
  <div class="workspace-page">
    <section class="page-intro">
      <div>
        <p>统一健康事件</p>
        <h2>健康时间线</h2>
        <span>查看本人或已授权亲友的机构体检报告与日常测量。</span>
      </div>
    </section>

    <el-card shadow="never">
      <div class="filter-row">
        <label class="filter-field">
          <span class="filter-field-label">查看成员</span>
          <el-select
            v-model="ownerValue"
            :loading="ownersLoading"
            placeholder="请选择本人或已授权亲友"
          >
            <el-option
              v-for="option in ownerOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </label>
      </div>
    </el-card>

    <el-alert
      v-if="errorMessage"
      :title="errorMessage"
      type="error"
      :closable="false"
      show-icon
    />

    <el-card shadow="never" v-loading="loading">
      <template #header>
        <strong>{{ selectedOwnerLabel }}的健康事件</strong>
      </template>
      <el-timeline v-if="items.length">
        <el-timeline-item
          v-for="event in items"
          :key="`${event.type}-${event.occurred_at}-${event.item?.id}`"
          :timestamp="format(event.occurred_at)"
          :type="event.type === 'institution_report' ? 'success' : 'primary'"
        >
          <h3>{{ event.title }}</h3>
          <el-button
            v-if="event.type === 'institution_report'"
            link
            type="primary"
            @click="router.push({ name: 'report-detail', params: { id: event.item.id } })"
          >
            查看标准化指标
          </el-button>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-else description="暂无时间线事件" />
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { fetchFriends } from "../api/friends";
import { fetchTimeline } from "../api/health";
import { useAuthStore } from "../stores/auth";
import {
  SELF_OWNER_VALUE,
  buildHealthOwnerOptions,
  ownerRequestParams,
} from "../utils/healthOwners";

const router = useRouter();
const authStore = useAuthStore();
const loading = ref(false);
const ownersLoading = ref(false);
const items = ref([]);
const ownerValue = ref(SELF_OWNER_VALUE);
const ownerOptions = ref(buildHealthOwnerOptions({}, authStore.user));
const errorMessage = ref("");

const selectedOwnerLabel = computed(
  () =>
    ownerOptions.value.find((option) => option.value === ownerValue.value)?.label ||
    "我本人"
);

const loadOwners = async () => {
  ownersLoading.value = true;
  try {
    const { data } = await fetchFriends();
    ownerOptions.value = buildHealthOwnerOptions(data, authStore.user);
    if (!ownerOptions.value.some((option) => option.value === ownerValue.value)) {
      ownerValue.value = SELF_OWNER_VALUE;
    }
  } finally {
    ownersLoading.value = false;
  }
};

const loadTimeline = async () => {
  loading.value = true;
  errorMessage.value = "";
  try {
    const { data } = await fetchTimeline(ownerRequestParams(ownerValue.value));
    items.value = data.items || [];
  } catch (error) {
    items.value = [];
    if (error?.response?.status === 403) {
      errorMessage.value = "该亲友的授权已失效，已切换回本人数据。";
      await loadOwners();
      ownerValue.value = SELF_OWNER_VALUE;
    } else {
      errorMessage.value = error?.response?.data?.message || "健康时间线加载失败";
    }
  } finally {
    loading.value = false;
  }
};

const format = (value) =>
  new Date(value).toLocaleString("zh-CN", { hour12: false });

watch(ownerValue, loadTimeline);

onMounted(async () => {
  try {
    await loadOwners();
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "亲友授权列表加载失败";
  }
  await loadTimeline();
});
</script>
