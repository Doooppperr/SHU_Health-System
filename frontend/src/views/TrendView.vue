<template>
  <div class="workspace-page user-platform-page">
    <section class="user-page-lead">
      <div>
        <span class="user-kicker">长期变化</span>
        <h2>看看身体在一段时间里的变化</h2>
        <p>同一指标汇总个人日常测量和各机构体检结果，每天只保留一个最有效的趋势点。</p>
      </div>
      <el-button type="primary" @click="router.push({ name: 'dashboard', query: { quick: 'measurement' } })">记录新测量</el-button>
    </section>

    <el-card shadow="never" class="user-panel user-filter-panel">
      <div class="trend-filter-grid-platform">
        <label class="filter-field">
          <span class="filter-field-label">查看谁的趋势</span>
          <el-select v-model="filters.owner_id">
            <el-option v-for="owner in owners" :key="String(owner.value)" :label="owner.label" :value="owner.value" />
          </el-select>
        </label>
        <label class="filter-field">
          <span class="filter-field-label">健康方向</span>
          <el-select v-model="filters.domain_id" placeholder="选择健康方向">
            <el-option v-for="domain in domains" :key="domain.id" :label="domain.name" :value="domain.id" />
          </el-select>
        </label>
        <label class="filter-field">
          <span class="filter-field-label">日期范围</span>
          <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD" range-separator="至" />
        </label>
        <label class="filter-field">
          <span class="filter-field-label">数据来源</span>
          <el-select v-model="filters.source"><el-option v-for="item in sourceOptions" :key="item.value" :label="item.label" :value="item.value" /></el-select>
        </label>
        <div class="filter-actions"><el-button type="primary" @click="apply">查看变化</el-button></div>
      </div>
    </el-card>

    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />

    <div class="trend-analysis-layout">
    <section v-loading="loading" class="trend-story-grid" aria-live="polite">
      <article v-for="entry in series" :key="entry.indicator.id" class="trend-story-card">
        <header>
          <div><span>健康指标</span><h3>{{ entry.indicator.name }}</h3></div>
          <small>{{ entry.indicator.unit || "无单位" }}</small>
        </header>

        <section class="trend-source-story">
          <div class="trend-source-story__heading">
            <el-tag type="success" effect="light">{{ selectedSourceLabel }}</el-tag>
            <div>
              <span>最近一次</span>
              <strong>{{ compact(entry.summary.latest) }} <small>{{ entry.indicator.unit }}</small></strong>
            </div>
            <div>
              <span>较上次</span>
              <strong :class="changeClass(entry.summary.change)">{{ changeLabel(entry.summary.change) }}</strong>
            </div>
          </div>

          <div class="trend-chart-platform">
            <HealthTrendChart :points="entry.points" :reference="entry.reference" :unit="entry.indicator.unit || ''" :indicator-name="entry.indicator.name" :source-name="selectedSourceLabel" />
            <div class="trend-reference-note">
              <strong>{{ entry.reference?.label || "暂无统一参考范围" }}</strong>
              <span v-if="entry.reference?.low != null && entry.reference?.high != null">{{ entry.reference.low }}–{{ entry.reference.high }} {{ entry.indicator.unit }}</span>
              <p>{{ entry.reference?.context }}</p>
              <a v-if="entry.reference?.source_url" :href="entry.reference.source_url" target="_blank" rel="noopener noreferrer">{{ entry.reference.source_title || "查看参考来源" }}</a>
            </div>
          </div>
        </section>
      </article>
    </section>
    <aside class="trend-ai-panel" aria-live="polite">
      <span class="user-kicker">AI 图表解读</span>
      <h3>结合当前筛选解释趋势</h3>
      <template v-if="!trendConsent">
        <p>确认后，本页面会把当前成员、健康方向和日期范围内的趋势数据发送至 DeepSeek。授权仅在本次页面停留期间有效。</p>
        <el-checkbox v-model="consentChecked">我已了解并同意本次页面分析</el-checkbox>
        <el-button type="primary" :disabled="!consentChecked || !series.length" @click="grantTrendConsent">授权并开始分析</el-button>
      </template>
      <template v-else>
        <div v-if="aiLoading" class="trend-ai-status"><span></span>{{ aiStatus || "正在分析当前图表…" }}</div>
        <div v-else-if="aiError" class="trend-ai-error"><p>{{ aiError }}</p><el-button plain @click="runTrendAnalysis(true)">重新分析</el-button></div>
        <p v-else-if="aiAnswer" class="trend-ai-answer">{{ aiAnswer }}</p>
        <p v-else>调整筛选后，这里会自动生成对应的趋势说明。</p>
        <small>AI 内容仅用于健康科普，不能替代医生诊断或治疗建议。</small>
      </template>
    </aside>
    </div>

    <div v-if="!loading && !series.length" class="user-empty-state user-empty-state--page">
      <span class="user-empty-state__icon">趋</span>
      <div><strong>这个健康方向还没有足够的数据</strong><p>连续记录几次后，就能在这里看见自己的变化。</p></div>
      <el-button type="primary" plain @click="router.push({ name: 'dashboard', query: { quick: 'measurement' } })">开始记录</el-button>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { fetchFriends } from "../api/friends";
import { streamAiTrendAnalysis } from "../api/ai";
import { fetchHealthDomains, fetchHealthTrends } from "../api/health";
import HealthTrendChart from "../components/HealthTrendChart.vue";
import { useAuthStore } from "../stores/auth";
import { buildHealthOwnerOptions, ownerRequestParams, SELF_OWNER_VALUE } from "../utils/healthOwners";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const domains = ref([]);
const owners = ref([]);
const sourceOptions = ref([{ value: "all", label: "全部来源" }, { value: "self", label: "个人日常测量" }, { value: "institution", label: "全部机构体检" }]);
const series = ref([]);
const loading = ref(false);
const error = ref("");
const dateRange = ref([]);
const consentChecked = ref(false);
const trendConsent = ref(false);
const aiLoading = ref(false);
const aiAnswer = ref("");
const aiError = ref("");
const aiStatus = ref("");
const analysisCache = new Map();
let analysisController;
let analysisTimer;
const filters = reactive({
  owner_id: route.query.owner_id ? String(route.query.owner_id) : SELF_OWNER_VALUE,
  domain_id: route.query.domain_id ? Number(route.query.domain_id) : null,
  source: typeof route.query.source === "string" ? route.query.source : "all",
});
const selectedSourceLabel = computed(() => sourceOptions.value.find((item) => item.value === filters.source)?.label || "全部来源");
if (route.query.start_date && route.query.end_date) dateRange.value = [route.query.start_date, route.query.end_date];

function sourceName(source) {
  return source?.type === "self" ? "本人记录" : [source?.name, source?.branch_name].filter(Boolean).join(" · ") || "体检机构";
}

function compact(value) {
  if (value === null || value === undefined) return "—";
  const number = Number(value);
  return Number.isInteger(number) ? number : Number(number.toFixed(2));
}

function changeLabel(value) {
  if (value === null || value === undefined) return "首次记录";
  const number = Number(value);
  if (Math.abs(number) < 0.005) return "基本持平";
  return `${number > 0 ? "上升" : "下降"} ${Math.abs(number).toFixed(2)}`;
}

function changeClass(value) {
  if (value === null || value === undefined || Math.abs(Number(value)) < 0.005) return "is-steady";
  return Number(value) > 0 ? "is-up" : "is-down";
}

function formatShortDate(value) {
  if (!value) return "—";
  const matched = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!matched) return "日期待核对";
  const date = new Date(Number(matched[1]), Number(matched[2]) - 1, Number(matched[3]));
  return Number.isNaN(date.getTime()) ? "日期待核对" : date.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
}

function cleanParams(value) {
  const result = { ...value };
  Object.keys(result).forEach((key) => {
    if (result[key] === null || result[key] === undefined || result[key] === "") delete result[key];
  });
  return result;
}

async function load() {
  if (!filters.domain_id) return;
  loading.value = true;
  error.value = "";
  try {
    const params = cleanParams({
      ...ownerRequestParams(filters.owner_id),
      source_type: filters.source.startsWith("institution") ? "institution" : filters.source,
      institution_id: filters.source.startsWith("institution:") ? Number(filters.source.split(":")[1]) : undefined,
      start_date: dateRange.value?.[0],
      end_date: dateRange.value?.[1],
    });
    const { data } = await fetchHealthTrends(filters.domain_id, params);
    series.value = data.series_by_indicator || [];
    sourceOptions.value = data.source_options || sourceOptions.value;
    if (trendConsent.value) scheduleTrendAnalysis();
  } catch (fetchError) {
    error.value = fetchError?.response?.data?.message || "健康趋势暂时没有加载成功，请稍后重试";
  } finally {
    loading.value = false;
  }
}

function analysisPayload() {
  return cleanParams({ ...ownerRequestParams(filters.owner_id), domain_id: filters.domain_id,
    source_type: filters.source.startsWith("institution") ? "institution" : filters.source,
    institution_id: filters.source.startsWith("institution:") ? Number(filters.source.split(":")[1]) : undefined,
    start_date: dateRange.value?.[0], end_date: dateRange.value?.[1], consent: true });
}

function scheduleTrendAnalysis() {
  clearTimeout(analysisTimer);
  analysisTimer = setTimeout(() => runTrendAnalysis(), 450);
}

async function grantTrendConsent() {
  trendConsent.value = true;
  await runTrendAnalysis();
}

async function runTrendAnalysis(force = false) {
  if (!trendConsent.value || !series.value.length) return;
  const payload = analysisPayload();
  const key = JSON.stringify(payload);
  if (!force && analysisCache.has(key)) {
    aiAnswer.value = analysisCache.get(key); aiError.value = ""; return;
  }
  analysisController?.abort();
  analysisController = new AbortController();
  aiLoading.value = true; aiAnswer.value = ""; aiError.value = ""; aiStatus.value = "";
  try {
    await streamAiTrendAnalysis(payload, { signal: analysisController.signal, onEvent(event) {
      if (event.event === "status") aiStatus.value = event.message || "正在分析当前图表…";
      if (event.event === "delta") aiAnswer.value += event.text || "";
    } });
    if (aiAnswer.value) analysisCache.set(key, aiAnswer.value);
  } catch (analysisError) {
    if (analysisError?.name !== "AbortError") aiError.value = analysisError?.message || "AI 暂时无法分析当前图表";
  } finally { aiLoading.value = false; }
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
  const [domainResponse, friendResponse] = await Promise.all([fetchHealthDomains(), fetchFriends()]);
  domains.value = domainResponse.data.items || [];
  filters.domain_id ||= domains.value[0]?.id;
  owners.value = buildHealthOwnerOptions(friendResponse.data, auth.user);
  await load();
});
onBeforeUnmount(() => { clearTimeout(analysisTimer); analysisController?.abort(); });
</script>
