<template>
  <div class="workspace-page">
    <section class="page-intro"><div><p>脱敏 · 只读</p><h2>机构指标趋势</h2><span>按归属人与标准指标查看本机构已确认档案的时间变化。</span></div></section>
    <el-alert v-if="errorMessage" :title="errorMessage" type="error" show-icon :closable="false" />
    <el-card shadow="never" class="filter-card">
      <div class="trend-filter-grid">
        <label class="filter-field">
          <span class="filter-field-label">档案归属人</span>
          <el-select v-model="selectedOwnerId" filterable placeholder="选择归属人" style="width:100%"><el-option v-for="owner in owners" :key="owner.id" :label="owner.label" :value="owner.id" /></el-select>
        </label>
        <label class="filter-field">
          <span class="filter-field-label">标准健康指标</span>
          <el-select v-model="selectedIndicatorId" filterable placeholder="选择标准指标" style="width:100%"><el-option v-for="item in indicators" :key="item.id" :label="`${item.code} - ${item.name}`" :value="item.id" /></el-select>
        </label>
        <el-button type="primary" :loading="loading" @click="loadTrend">查询趋势</el-button>
      </div>
    </el-card>

    <el-card v-if="result" shadow="never" class="dashboard-card">
      <template #header><div class="card-heading"><div><h3>{{ result.indicator?.code }} · {{ result.indicator?.name }}</h3><p>{{ result.owner?.display_name || result.owner?.username || selectedOwnerLabel }}</p></div><el-tag>{{ result.summary?.count ?? result.series?.length ?? 0 }} 条记录</el-tag></div></template>
      <div ref="resultContentRef" class="trend-result-content">
        <div v-if="result.indicator?.value_type === 'numeric'" ref="chartRef" class="trend-chart" />
        <el-alert v-else title="该指标不是数值型，以下仅展示历史明细。" type="info" :closable="false" />
        <el-descriptions :column="summaryColumns" border class="trend-summary">
          <el-descriptions-item label="最新值">{{ result.summary?.latest ?? "-" }}</el-descriptions-item>
          <el-descriptions-item label="最小值">{{ result.summary?.min ?? "-" }}</el-descriptions-item>
          <el-descriptions-item label="最大值">{{ result.summary?.max ?? "-" }}</el-descriptions-item>
          <el-descriptions-item label="参考范围">{{ result.summary?.reference_low ?? "-" }} ~ {{ result.summary?.reference_high ?? "-" }}</el-descriptions-item>
        </el-descriptions>
        <el-table :data="result.series || []" empty-text="暂无趋势数据">
          <el-table-column prop="exam_date" label="体检日期" width="130" /><el-table-column prop="value" label="指标值" min-width="120" /><el-table-column label="异常" width="100"><template #default="scope"><el-tag :type="scope.row.is_abnormal ? 'danger' : 'success'">{{ scope.row.is_abnormal ? "异常" : "正常" }}</el-tag></template></el-table-column><el-table-column label="档案 ID" width="120"><template #default="scope">{{ formatRecordDisplayId(scope.row) }}</template></el-table-column>
        </el-table>
      </div>
    </el-card>
    <el-empty v-else-if="!loading" description="选择归属人与指标后查看趋势" />
    <el-alert title="机构趋势不用于健康 AI" description="机构管理员无法把用户健康数据提交给 AI；趋势仅供机构内部了解已确认的标准化数据。" type="warning" show-icon :closable="false" />
  </div>
</template>

<script setup>
import { storeToRefs } from "pinia";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { fetchIndicatorDicts } from "../../api/indicators";
import { fetchOrgHealthRecords, fetchOrgHealthTrends } from "../../api/org";
import { useAppearanceStore } from "../../stores/appearance";
import { buildTrendChartOption } from "../../utils/chartAppearance";
import { initTrendChart } from "../../utils/echartsRuntime";
import { formatRecordDisplayId } from "../../utils/recordDisplayId";

const appearanceStore = useAppearanceStore();
const { effectiveTheme, careMode } = storeToRefs(appearanceStore);
const indicators = ref([]); const owners = ref([]); const selectedOwnerId = ref(null); const selectedIndicatorId = ref(null); const result = ref(null); const loading = ref(false); const errorMessage = ref(""); const chartRef = ref(null); const resultContentRef = ref(null); let chart = null;
let chartElement = null; let resizeObserver = null; let windowResizeListening = false;
const summaryContainerWidth = ref(Number.POSITIVE_INFINITY);
let summaryResizeObserver = null; let summaryWindowResizeListening = false;
const selectedOwnerLabel = computed(() => owners.value.find((item) => item.id === selectedOwnerId.value)?.label || "归属人");
const summaryColumns = computed(() => {
  if (summaryContainerWidth.value <= 520) return 1;
  return careMode.value || summaryContainerWidth.value <= 900 ? 2 : 4;
});
const measureSummaryContainer = (entries) => {
  const observedWidth = entries?.[0]?.contentRect?.width;
  const measuredWidth = Number.isFinite(observedWidth) ? observedWidth : resultContentRef.value?.getBoundingClientRect().width;
  if (Number.isFinite(measuredWidth)) summaryContainerWidth.value = measuredWidth;
};
const unbindSummaryResize = () => {
  summaryResizeObserver?.disconnect();
  if (summaryWindowResizeListening) { window.removeEventListener("resize", measureSummaryContainer); summaryWindowResizeListening = false; }
  summaryContainerWidth.value = Number.POSITIVE_INFINITY;
};
const bindSummaryResize = (element) => {
  unbindSummaryResize(); if (!element) return;
  if (typeof ResizeObserver === "function") { summaryResizeObserver ||= new ResizeObserver(measureSummaryContainer); summaryResizeObserver.observe(element); }
  else { window.addEventListener("resize", measureSummaryContainer); summaryWindowResizeListening = true; }
  measureSummaryContainer();
};
async function loadBase() {
  const [dictRes, recordRes] = await Promise.all([fetchIndicatorDicts(), fetchOrgHealthRecords({ page: 1, page_size: 100 })]);
  indicators.value = dictRes.data.items || [];
  const map = new Map();
  (recordRes.data.items || []).forEach((item) => { if (!map.has(item.owner_id)) map.set(item.owner_id, { id: item.owner_id, label: item.owner_display_name || item.owner?.display_name || item.owner?.username || `用户 ${item.owner_id}` }); });
  owners.value = [...map.values()];
  selectedOwnerId.value ||= owners.value[0]?.id || null; selectedIndicatorId.value ||= indicators.value[0]?.id || null;
}

const resizeChart = () => chart?.resize();
function unbindChartResize() {
  resizeObserver?.disconnect();
  if (windowResizeListening) {
    window.removeEventListener("resize", resizeChart);
    windowResizeListening = false;
  }
  chartElement = null;
}
function disposeChart() {
  unbindChartResize();
  chart?.dispose();
  chart = null;
}
function bindChartResize(element) {
  if (chartElement === element) return;
  unbindChartResize();
  chartElement = element;
  if (typeof ResizeObserver === "function") {
    resizeObserver ||= new ResizeObserver(resizeChart);
    resizeObserver.observe(element);
  } else {
    window.addEventListener("resize", resizeChart);
    windowResizeListening = true;
  }
}
async function renderChart() {
  await nextTick();
  if (!chartRef.value || result.value?.indicator?.value_type !== "numeric") { disposeChart(); return; }
  if (chart && chartElement !== chartRef.value) disposeChart();
  chart ||= initTrendChart(chartRef.value);
  bindChartResize(chartRef.value);
  const series = result.value.series || [];
  const marks = []; const summary = result.value.summary || {};
  if (summary.reference_low != null) marks.push({ yAxis: summary.reference_low, name: "参考下限" });
  if (summary.reference_high != null) marks.push({ yAxis: summary.reference_high, name: "参考上限" });
  chart.setOption(
    buildTrendChartOption({
      theme: effectiveTheme.value,
      careMode: careMode.value,
      accent: "institution",
      xAxisData: series.map((item) => item.exam_date),
      yAxisData: series.map((item) => item.numeric_value),
      unit: result.value.indicator?.unit || "",
      referenceLines: marks,
    }),
    { notMerge: true, lazyUpdate: false }
  );
}
async function loadTrend() {
  if (!selectedOwnerId.value || !selectedIndicatorId.value) { errorMessage.value = "请先选择归属人与指标"; return; }
  loading.value = true; errorMessage.value = "";
  try { const { data } = await fetchOrgHealthTrends({ owner_id: selectedOwnerId.value, indicator_dict_id: selectedIndicatorId.value }); result.value = data; await renderChart(); }
  catch (error) { result.value = null; errorMessage.value = error?.response?.data?.message || "机构趋势加载失败"; }
  finally { loading.value = false; }
}
onMounted(async () => { try { await loadBase(); if (selectedOwnerId.value && selectedIndicatorId.value) await loadTrend(); } catch (error) { errorMessage.value = error?.response?.data?.message || "趋势页面初始化失败"; } });
watch([effectiveTheme, careMode], () => { if (result.value?.indicator?.value_type === "numeric") void renderChart(); });
watch(chartRef, (element) => { if (!element && chart) disposeChart(); });
watch(resultContentRef, bindSummaryResize, { flush: "post" });
onBeforeUnmount(() => { unbindSummaryResize(); disposeChart(); resizeObserver = null; summaryResizeObserver = null; });
</script>
