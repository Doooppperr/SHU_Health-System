<template>
  <div class="workspace-page user-platform-page">
    <section class="user-page-lead">
      <div>
        <span class="user-kicker">我的体检档案</span>
        <h2>每次机构体检，都有清楚的来源和日期</h2>
        <p>这里只整理机构正式归档的指标、结论和检查附件；个人测量请到健康趋势查看。</p>
      </div>
      <el-button type="primary" plain @click="router.push({ name: 'trends' })">查看健康趋势</el-button>
    </section>

    <el-card shadow="never" class="user-panel user-filter-panel">
      <div class="health-data-filter-grid">
        <label class="filter-field">
          <span class="filter-field-label">查看谁的资料</span>
          <el-select v-model="filters.owner_id" @change="filters.page = 1">
            <el-option v-for="item in owners" :key="String(item.value)" :label="item.label" :value="item.value" />
          </el-select>
        </label>
        <label class="filter-field">
          <span class="filter-field-label">日期范围</span>
          <div class="health-data-date-filter">
            <el-select v-model="datePreset" aria-label="日期范围快捷选择" @change="applyDatePreset">
              <el-option label="全部记录" value="all" />
              <el-option label="近一周" value="week" />
              <el-option label="近一个月" value="month" />
              <el-option label="近半年" value="half_year" />
              <el-option label="自定义范围" value="custom" />
            </el-select>
            <el-date-picker
              v-if="datePreset === 'custom'"
              v-model="dateRange"
              type="daterange"
              unlink-panels
              clearable
              value-format="YYYY-MM-DD"
              range-separator="至"
              start-placeholder="开始日期"
              end-placeholder="结束日期"
              @change="applyCustomDateRange"
            />
          </div>
        </label>
        <label class="filter-field">
          <span class="filter-field-label">资料来源</span>
          <el-select v-model="filters.institution_id" clearable placeholder="全部来源">
            <el-option v-for="item in institutions" :key="item.id" :label="`${item.name} · ${item.branch_name}`" :value="item.id" />
          </el-select>
        </label>
        <label class="filter-field">
          <span class="filter-field-label">健康方向</span>
          <el-select v-model="filters.domain_id" clearable placeholder="全部方向">
            <el-option v-for="item in domains" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </label>
        <div class="filter-actions"><el-button type="primary" @click="applyFilters(true)">筛选资料</el-button></div>
      </div>
    </el-card>

    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />

    <section v-loading="loading" class="health-record-grid" aria-live="polite">
      <article v-for="item in items" :key="item.health_data_id" class="health-record-card">
        <header>
          <div class="health-record-card__date">
            <strong>{{ dateParts(item.business_date).day }}</strong>
            <span>{{ dateParts(item.business_date).month }}</span>
          </div>
          <div class="health-record-card__heading">
            <el-tag type="success" effect="light">机构体检</el-tag>
            <h3>{{ cardTitle(item) }}</h3>
            <p>{{ sourceLabel(item.source_type, item.source) }}</p>
          </div>
        </header>

        <div class="health-record-card__domains">
          <span v-for="domain in item.domains" :key="domain.id">{{ domain.name }}</span>
          <span v-if="!item.domains?.length">综合体检</span>
        </div>

        <div class="health-record-card__counts">
          <div><strong>{{ item.indicator_count }}</strong><span>项指标</span></div>
          <div><strong>{{ item.text_result_count }}</strong><span>条结论</span></div>
          <div><strong>{{ item.asset_count }}</strong><span>个附件</span></div>
        </div>

        <footer>
          <span>由体检分院确认并正式归档</span>
          <el-button type="primary" plain @click="openDetail(item)">查看详情</el-button>
        </footer>
      </article>

      <div v-if="!loading && !items.length" class="user-empty-state user-empty-state--page">
        <span class="user-empty-state__icon">档</span>
        <div><strong>没有找到符合条件的体检数据</strong><p>可以调整筛选条件，或前往体检机构预约新的服务。</p></div>
        <el-button type="primary" plain @click="router.push({ name: 'institutions' })">查看体检机构</el-button>
      </div>
    </section>

    <div v-if="pagination.total > pagination.page_size" class="user-pagination">
      <el-pagination
        v-model:current-page="filters.page"
        :page-size="pagination.page_size"
        :total="pagination.total"
        layout="prev, pager, next, total"
        @current-change="applyFilters(false)"
      />
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { fetchFriends } from "../api/friends";
import { fetchHealthData, fetchHealthDomains } from "../api/health";
import { fetchInstitutions } from "../api/institutions";
import { useAuthStore } from "../stores/auth";
import { buildHealthOwnerOptions, ownerRequestParams, SELF_OWNER_VALUE, withOwnerRequestParams } from "../utils/healthOwners";
import { sourceLabel } from "../utils/userPlatform";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const loading = ref(false);
const error = ref("");
const items = ref([]);
const domains = ref([]);
const institutions = ref([]);
const owners = ref([]);
const dateRange = ref([]);
const DATE_PRESETS = new Set(["all", "week", "month", "half_year", "custom"]);
const datePreset = ref(DATE_PRESETS.has(String(route.query.date_preset)) ? String(route.query.date_preset) : "all");
const pagination = reactive({ page: 1, page_size: 15, total: 0 });
const filters = reactive({
  owner_id: route.query.owner_id ? String(route.query.owner_id) : SELF_OWNER_VALUE,
  institution_id: route.query.institution_id ? Number(route.query.institution_id) : null,
  domain_id: route.query.domain_id ? Number(route.query.domain_id) : null,
  page: Number(route.query.page) || 1,
});
if (route.query.start_date && route.query.end_date) {
  dateRange.value = [route.query.start_date, route.query.end_date];
  if (!DATE_PRESETS.has(String(route.query.date_preset))) datePreset.value = "custom";
}

function cleanParams(value) {
  const result = { ...value };
  Object.keys(result).forEach((key) => {
    if (result[key] === null || result[key] === undefined || result[key] === "") delete result[key];
  });
  return result;
}

function dateParts(value) {
  const matched = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!matched) return { day: "—", month: "日期待核对" };
  const date = new Date(Number(matched[1]), Number(matched[2]) - 1, Number(matched[3]));
  return {
    day: String(date.getDate()).padStart(2, "0"),
    month: date.toLocaleDateString("zh-CN", { month: "short", year: "numeric" }),
  };
}

function cardTitle(item) {
  return item.package?.name || "体检健康数据";
}

function localDate(value = new Date()) {
  return [
    value.getFullYear(),
    String(value.getMonth() + 1).padStart(2, "0"),
    String(value.getDate()).padStart(2, "0"),
  ].join("-");
}

function dateOffset(days) {
  const value = new Date();
  value.setHours(0, 0, 0, 0);
  value.setDate(value.getDate() - days);
  return localDate(value);
}

function dateFilterParams() {
  const offsets = { week: 7, month: 30, half_year: 183 };
  if (offsets[datePreset.value]) {
    return { start_date: dateOffset(offsets[datePreset.value]), end_date: localDate() };
  }
  if (datePreset.value === "custom" && dateRange.value?.length === 2) {
    return { start_date: dateRange.value[0], end_date: dateRange.value[1] };
  }
  return {};
}

function openDetail(item) {
  router.push({
    name: "health-data-detail",
    params: { id: item.health_data_id },
    query: ownerRequestParams(filters.owner_id),
  });
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const params = cleanParams(withOwnerRequestParams({
      ...filters,
      ...dateFilterParams(),
    }, filters.owner_id));
    const { data } = await fetchHealthData(params);
    items.value = data.items || [];
    Object.assign(pagination, data.pagination || {});
  } catch (fetchError) {
    error.value = fetchError?.response?.data?.message || "健康资料暂时没有加载成功，请稍后重试";
  } finally {
    loading.value = false;
  }
}

async function applyDatePreset() {
  if (datePreset.value !== "custom") dateRange.value = [];
  await applyFilters(true);
}

async function applyCustomDateRange() {
  await applyFilters(true);
}

async function applyFilters(resetPage = false) {
  if (resetPage) filters.page = 1;
  const query = cleanParams({
    ...filters,
    owner_id: filters.owner_id === SELF_OWNER_VALUE ? undefined : filters.owner_id,
    date_preset: datePreset.value === "all" ? undefined : datePreset.value,
    ...dateFilterParams(),
  });
  await router.replace({ query });
  await load();
}

onMounted(async () => {
  const [domainResponse, institutionResponse, friendResponse] = await Promise.all([
    fetchHealthDomains(),
    fetchInstitutions(),
    fetchFriends(),
  ]);
  domains.value = domainResponse.data.items || [];
  institutions.value = institutionResponse.data.items || [];
  owners.value = buildHealthOwnerOptions(friendResponse.data, auth.user);
  await load();
});
</script>

<style scoped>
.health-record-grid { grid-template-columns: minmax(0, 1fr); }
.health-record-card { width: 100%; }
</style>
