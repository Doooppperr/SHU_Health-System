<template>
  <div class="workspace-page user-platform-page">
    <section class="user-page-lead">
      <div>
        <span class="user-kicker">健康历程</span>
        <h2>沿着时间，看见每一次健康行动</h2>
        <p>体检按一次行程完整保留，日常测量按天收拢，不让零散数据淹没真正重要的变化。</p>
      </div>
      <el-button type="primary" @click="router.push({ name: 'dashboard', query: { quick: 'measurement' } })">记录今日测量</el-button>
    </section>

    <el-card shadow="never" class="user-panel timeline-filter-card">
      <el-segmented v-model="filters.record_type" :options="recordTypeOptions" @change="recordTypeChanged" />
      <div class="timeline-filter-grid">
        <label class="filter-field">
          <span class="filter-field-label">查看谁的记录</span>
          <el-select v-model="filters.owner_id">
            <el-option v-for="owner in owners" :key="String(owner.value)" :label="owner.label" :value="owner.value" />
          </el-select>
        </label>
        <label class="filter-field">
          <span class="filter-field-label">日期范围</span>
          <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD" range-separator="至" />
        </label>
        <label v-if="filters.record_type !== 'self'" class="filter-field">
          <span class="filter-field-label">体检机构</span>
          <el-select v-model="filters.institution_id" clearable placeholder="全部机构">
            <el-option v-for="item in institutions" :key="item.id" :label="`${item.name} · ${item.branch_name}`" :value="item.id" />
          </el-select>
        </label>
        <label v-if="filters.record_type !== 'self'" class="filter-field filter-field--compact">
          <span class="filter-field-label">进度</span>
          <el-select v-model="filters.status" clearable placeholder="全部进度">
            <el-option v-for="(meta, key) in APPOINTMENT_STATUS" :key="key" :label="meta.label" :value="key" />
          </el-select>
        </label>
        <div class="filter-actions"><el-button type="primary" @click="apply">筛选记录</el-button></div>
      </div>
    </el-card>

    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />

    <section v-loading="loading" class="health-journey" aria-live="polite">
      <article v-for="record in visibleItems" :key="record.key" class="health-journey-card" :class="`health-journey-card--${record.kind}`">
        <div class="health-journey-card__rail">
          <span>{{ record.kind === "self" ? "记" : "检" }}</span>
        </div>
        <div class="health-journey-card__content">
          <header>
            <div>
              <time>{{ formatDate(record.businessDate) }}</time>
              <h3>{{ recordHeading(record) }}</h3>
            </div>
            <el-tag v-if="record.kind === 'exam'" :type="appointmentMeta(record.item?.status).type" effect="light">
              {{ appointmentMeta(record.item?.status).label }}
            </el-tag>
            <el-tag v-else type="info" effect="light">本人记录</el-tag>
          </header>

          <template v-if="record.kind === 'exam'">
            <p class="health-journey-card__summary">
              {{ record.item?.institution?.name }} · {{ record.item?.institution?.branch_name }}
              <span v-if="record.item?.user?.name"> · {{ record.item.user.name }}</span>
            </p>
            <ol class="journey-steps" :aria-label="`${recordHeading(record)}进度`">
              <li v-for="(event, index) in compactEvents(record)" :key="`${event.type}-${event.occurred_at}-${index}`" :class="{ 'is-latest': index === compactEvents(record).length - 1 }">
                <span></span>
                <div><strong>{{ event.message }}</strong><small>{{ formatDateTime(event.occurred_at) }}</small></div>
              </li>
            </ol>
            <div class="health-journey-card__footer">
              <p>{{ appointmentMeta(record.item?.status).hint }}</p>
              <el-button v-if="record.detailId" type="primary" plain @click="openDetail(record)">查看本次健康数据</el-button>
              <el-button v-else-if="record.item?.status === 'unfulfilled'" plain @click="router.push({ name: 'appointments' })">管理预约</el-button>
            </div>
          </template>

          <template v-else>
            <p class="health-journey-card__summary">当天共记录 {{ record.indicatorCount }} 项指标，已自动整理到健康趋势。</p>
            <div class="journey-domain-list">
              <span v-for="domain in record.domains" :key="domain.id">{{ domain.name }}</span>
              <span v-if="!record.domains.length">日常健康</span>
            </div>
            <div class="health-journey-card__footer">
              <p>同一天的多次测量会保留原始时间和值。</p>
              <el-button v-if="record.detailId" type="primary" plain @click="openDetail(record)">查看当天记录</el-button>
            </div>
          </template>
        </div>
      </article>

      <div v-if="!loading && !visibleItems.length" class="user-empty-state user-empty-state--page">
        <span class="user-empty-state__icon">历</span>
        <div><strong>这段时间还没有健康记录</strong><p>可以调整筛选范围，或从一次日常测量开始。</p></div>
        <el-button type="primary" plain @click="router.push({ name: 'dashboard', query: { quick: 'measurement' } })">记录测量</el-button>
      </div>
    </section>

    <div v-if="pagination.total > pagination.page_size" class="user-pagination">
      <el-pagination
        v-model:current-page="filters.page"
        :page-size="pagination.page_size"
        :total="pagination.total"
        layout="prev, pager, next, total"
        @current-change="apply"
      />
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { fetchFriends } from "../api/friends";
import { fetchTimeline } from "../api/health";
import { fetchInstitutions } from "../api/institutions";
import { useAuthStore } from "../stores/auth";
import { buildHealthOwnerOptions, ownerRequestParams, SELF_OWNER_VALUE } from "../utils/healthOwners";
import {
  APPOINTMENT_STATUS,
  appointmentMeta,
  formatDate,
  formatDateTime,
  normalizeTimelineEntry,
} from "../utils/userPlatform";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const owners = ref([]);
const institutions = ref([]);
const items = ref([]);
const loading = ref(false);
const error = ref("");
const dateRange = ref([]);
const pagination = reactive({ page: 1, page_size: 15, total: 0 });
const recordTypeOptions = [
  { label: "全部记录", value: "all" },
  { label: "体检记录", value: "exam" },
  { label: "个人记录", value: "self" },
];
const filters = reactive({
  record_type: ["all", "exam", "self"].includes(route.query.record_type) ? route.query.record_type : "all",
  owner_id: route.query.owner_id ? String(route.query.owner_id) : SELF_OWNER_VALUE,
  institution_id: route.query.institution_id ? Number(route.query.institution_id) : null,
  status: route.query.status || null,
  page: Number(route.query.page) || 1,
});
if (route.query.start_date && route.query.end_date) dateRange.value = [route.query.start_date, route.query.end_date];

const visibleItems = computed(() => items.value);

function recordHeading(record) {
  if (record.kind === "self") return "当天的日常测量";
  return record.item?.package_name || "体检安排";
}

function compactEvents(record) {
  return (record.events || []).slice(-4);
}

function openDetail(record) {
  router.push({
    name: "health-data-detail",
    params: { id: record.detailId },
    query: ownerRequestParams(filters.owner_id),
  });
}

async function recordTypeChanged() {
  filters.page = 1;
  if (filters.record_type === "self") {
    filters.institution_id = null;
    filters.status = null;
  }
  await apply();
}

function cleanParams(value) {
  const result = { ...value };
  Object.keys(result).forEach((key) => {
    if (result[key] === null || result[key] === undefined || result[key] === "") delete result[key];
  });
  return result;
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const params = cleanParams({
      ...ownerRequestParams(filters.owner_id),
      start_date: dateRange.value?.[0],
      end_date: dateRange.value?.[1],
      page: filters.page,
      page_size: pagination.page_size,
      record_type: filters.record_type,
      institution_id: filters.record_type === "self" ? null : filters.institution_id,
      status: filters.record_type === "self" ? null : filters.status,
    });
    const { data } = await fetchTimeline(params);
    items.value = (data.items || []).map(normalizeTimelineEntry);
    Object.assign(pagination, data.pagination || {});
  } catch (fetchError) {
    error.value = fetchError?.response?.data?.message || "健康历程暂时没有加载成功，请稍后重试";
  } finally {
    loading.value = false;
  }
}

async function apply() {
  const query = cleanParams({
    ...filters,
    owner_id: filters.owner_id === SELF_OWNER_VALUE ? undefined : filters.owner_id,
    start_date: dateRange.value?.[0],
    end_date: dateRange.value?.[1],
  });
  await router.replace({ query });
  await load();
}

onMounted(async () => {
  const [friendResponse, institutionResponse] = await Promise.all([fetchFriends(), fetchInstitutions()]);
  owners.value = buildHealthOwnerOptions(friendResponse.data, auth.user);
  institutions.value = institutionResponse.data.items || [];
  await load();
});
</script>
