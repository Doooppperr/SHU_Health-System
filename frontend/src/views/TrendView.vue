<template>
  <div class="home-shell">
    <el-card class="home-card">
      <template #header>
        <div class="top-bar">
          <span>指标趋势分析</span>
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

      <el-card shadow="never" style="margin-bottom: 16px">
        <div class="trend-filter-grid">
          <label class="filter-field">
            <span class="filter-field-label">档案归属人</span>
            <el-select v-model="selectedOwnerId" placeholder="选择档案归属人" style="width: 100%">
              <el-option v-for="owner in ownerOptions" :key="owner.id" :label="owner.label" :value="owner.id" />
            </el-select>
          </label>

          <label class="filter-field">
            <span class="filter-field-label">健康指标</span>
            <el-select v-model="selectedIndicatorId" filterable placeholder="选择指标" style="width: 100%">
              <el-option
                v-for="item in indicatorOptions"
                :key="item.id"
                :label="`${item.code} - ${item.name}`"
                :value="item.id"
              />
            </el-select>
          </label>

          <el-button type="primary" :loading="loading" @click="loadTrend">查询趋势</el-button>
        </div>
      </el-card>

      <el-card shadow="never" v-if="trendResult">
        <template #header>
          <div class="top-bar">
            <span>
              {{ trendResult.owner.username }} · {{ trendResult.indicator.code }} - {{ trendResult.indicator.name }}
            </span>
            <el-tag>{{ trendResult.summary.count }} 条记录</el-tag>
          </div>
        </template>

        <div ref="resultContentRef" class="trend-result-content">
          <div v-if="trendResult.indicator.value_type !== 'numeric'" class="trend-tip">
            当前指标为文本型，无法绘制数值折线图，仅展示历史明细。
          </div>

          <div v-else ref="chartRef" class="trend-chart"></div>

          <el-descriptions :column="summaryColumns" border size="small" style="margin-bottom: 16px">
            <el-descriptions-item label="最新值">{{ trendResult.summary.latest ?? "-" }}</el-descriptions-item>
            <el-descriptions-item label="最小值">{{ trendResult.summary.min ?? "-" }}</el-descriptions-item>
            <el-descriptions-item label="最大值">{{ trendResult.summary.max ?? "-" }}</el-descriptions-item>
            <el-descriptions-item label="参考范围">
              {{ trendResult.summary.reference_low ?? "-" }} ~ {{ trendResult.summary.reference_high ?? "-" }}
            </el-descriptions-item>
          </el-descriptions>

          <el-table :data="trendResult.series" border empty-text="暂无趋势数据">
            <el-table-column prop="exam_date" label="体检日期" width="130" />
            <el-table-column prop="value" label="指标值" min-width="120" />
            <el-table-column label="是否异常" width="110">
              <template #default="scope">
                <el-tag :type="scope.row.is_abnormal ? 'danger' : 'success'">
                  {{ scope.row.is_abnormal ? "异常" : "正常" }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="source" label="来源" width="90" />
            <el-table-column label="档案ID" width="120">
              <template #default="scope">{{ formatRecordDisplayId(scope.row) }}</template>
            </el-table-column>
          </el-table>
        </div>
      </el-card>
    </el-card>
  </div>
</template>

<script setup>
import { storeToRefs } from "pinia";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import * as echarts from "echarts";

import MainNavActions from "../components/MainNavActions.vue";
import { fetchFriends } from "../api/friends";
import { fetchIndicatorDicts } from "../api/indicators";
import { fetchIndicatorTrend } from "../api/trends";
import { useAppearanceStore } from "../stores/appearance";
import { useAuthStore } from "../stores/auth";
import { buildTrendChartOption } from "../utils/chartAppearance";
import { formatRecordDisplayId } from "../utils/recordDisplayId";

const router = useRouter();
const authStore = useAuthStore();
const appearanceStore = useAppearanceStore();
const { effectiveTheme, careMode } = storeToRefs(appearanceStore);

const loading = ref(false);
const errorMessage = ref("");
const trendResult = ref(null);

const indicatorOptions = ref([]);
const manageableFriends = ref([]);

const selectedOwnerId = ref(null);
const selectedIndicatorId = ref(null);

const chartRef = ref(null);
const resultContentRef = ref(null);
let chartInstance = null;
let chartElement = null;
let resizeObserver = null;
let windowResizeListening = false;
const summaryContainerWidth = ref(Number.POSITIVE_INFINITY);
let summaryResizeObserver = null;
let summaryWindowResizeListening = false;

const summaryColumns = computed(() => {
  if (summaryContainerWidth.value <= 520) {
    return 1;
  }
  return careMode.value || summaryContainerWidth.value <= 900 ? 2 : 4;
});

const ownerOptions = computed(() => {
  const selfOption = authStore.user
    ? [{ id: authStore.user.id, label: `${authStore.user.username}（本人）` }]
    : [];
  const friendOptions = manageableFriends.value
    .filter((item) => item.friend_user?.id)
    .map((item) => ({
      id: item.friend_user.id,
      label: `${item.friend_user.username}（亲友）`,
    }));
  return [...selfOption, ...friendOptions];
});

const measureSummaryContainer = (entries) => {
  const observedWidth = entries?.[0]?.contentRect?.width;
  const measuredWidth = Number.isFinite(observedWidth)
    ? observedWidth
    : resultContentRef.value?.getBoundingClientRect().width;
  if (Number.isFinite(measuredWidth)) {
    summaryContainerWidth.value = measuredWidth;
  }
};

const unbindSummaryResize = () => {
  summaryResizeObserver?.disconnect();
  if (summaryWindowResizeListening) {
    window.removeEventListener("resize", measureSummaryContainer);
    summaryWindowResizeListening = false;
  }
  summaryContainerWidth.value = Number.POSITIVE_INFINITY;
};

const bindSummaryResize = (element) => {
  unbindSummaryResize();
  if (!element) {
    return;
  }
  if (typeof ResizeObserver === "function") {
    summaryResizeObserver ||= new ResizeObserver(measureSummaryContainer);
    summaryResizeObserver.observe(element);
  } else {
    window.addEventListener("resize", measureSummaryContainer);
    summaryWindowResizeListening = true;
  }
  measureSummaryContainer();
};

const resizeChart = () => {
  chartInstance?.resize();
};

const unbindChartResize = () => {
  resizeObserver?.disconnect();
  if (windowResizeListening) {
    window.removeEventListener("resize", resizeChart);
    windowResizeListening = false;
  }
  chartElement = null;
};

const disposeChart = () => {
  unbindChartResize();
  chartInstance?.dispose();
  chartInstance = null;
};

const bindChartResize = (element) => {
  if (chartElement === element) {
    return;
  }

  unbindChartResize();
  chartElement = element;
  if (typeof ResizeObserver === "function") {
    resizeObserver ||= new ResizeObserver(resizeChart);
    resizeObserver.observe(element);
  } else {
    window.addEventListener("resize", resizeChart);
    windowResizeListening = true;
  }
};

const renderChart = async () => {
  await nextTick();
  if (
    !trendResult.value ||
    trendResult.value.indicator.value_type !== "numeric" ||
    !chartRef.value
  ) {
    disposeChart();
    return;
  }

  if (chartInstance && chartElement !== chartRef.value) {
    disposeChart();
  }
  if (!chartInstance) {
    chartInstance = echarts.init(chartRef.value);
  }
  bindChartResize(chartRef.value);

  const xAxisData = trendResult.value.series.map((item) => item.exam_date);
  const yAxisData = trendResult.value.series.map((item) => item.numeric_value);

  const referenceLines = [];
  if (trendResult.value.summary.reference_low !== null) {
    referenceLines.push({
      yAxis: trendResult.value.summary.reference_low,
      name: "参考下限",
      lineStyle: { type: "dashed" },
    });
  }
  if (trendResult.value.summary.reference_high !== null) {
    referenceLines.push({
      yAxis: trendResult.value.summary.reference_high,
      name: "参考上限",
      lineStyle: { type: "dashed" },
    });
  }

  chartInstance.setOption(
    buildTrendChartOption({
      theme: effectiveTheme.value,
      careMode: careMode.value,
      accent: "user",
      xAxisData,
      yAxisData,
      unit: trendResult.value.indicator.unit || "",
      referenceLines,
    }),
    { notMerge: true, lazyUpdate: false }
  );
};

const loadBaseData = async () => {
  if (!authStore.user) {
    await authStore.fetchMe();
  }

  const [friendsRes, indicatorsRes] = await Promise.all([fetchFriends(), fetchIndicatorDicts()]);
  manageableFriends.value = friendsRes.data.manageable || [];
  indicatorOptions.value = indicatorsRes.data.items || [];

  if (!selectedOwnerId.value && authStore.user) {
    selectedOwnerId.value = authStore.user.id;
  }
  if (!selectedIndicatorId.value && indicatorOptions.value.length) {
    selectedIndicatorId.value = indicatorOptions.value[0].id;
  }
};

const loadTrend = async () => {
  if (!selectedOwnerId.value || !selectedIndicatorId.value) {
    errorMessage.value = "请先选择归属人和指标";
    return;
  }

  loading.value = true;
  errorMessage.value = "";

  try {
    const { data } = await fetchIndicatorTrend(selectedIndicatorId.value, selectedOwnerId.value);
    trendResult.value = data;
    await renderChart();
  } catch (error) {
    trendResult.value = null;
    errorMessage.value = error?.response?.data?.message || "趋势数据加载失败";
  } finally {
    loading.value = false;
  }
};

const goInstitutions = () => {
  router.push({ name: "institutions" });
};

const goRecords = () => {
  router.push({ name: "records" });
};

const goFriends = () => {
  router.push({ name: "friends" });
};

const goProfile = () => {
  router.push({ name: "profile" });
};

const logout = () => {
  authStore.logout();
  router.push({ name: "login" });
};

onMounted(async () => {
  try {
    await loadBaseData();
    await loadTrend();
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "趋势页面初始化失败";
  }
});

watch([effectiveTheme, careMode], () => {
  if (trendResult.value?.indicator?.value_type === "numeric") {
    void renderChart();
  }
});

watch(chartRef, (element) => {
  if (!element && chartInstance) {
    disposeChart();
  }
});

watch(resultContentRef, bindSummaryResize, { flush: "post" });

onBeforeUnmount(() => {
  unbindSummaryResize();
  disposeChart();
  resizeObserver = null;
  summaryResizeObserver = null;
});
</script>
