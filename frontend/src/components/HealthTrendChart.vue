<template>
  <div ref="container" class="health-trend-chart" role="img" :aria-label="ariaLabel"></div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useAppearanceStore } from "../stores/appearance";
import { initTrendChart } from "../utils/echartsRuntime";
import { buildTrendChartOption } from "../utils/chartAppearance";
import { formatBusinessDate } from "../utils/userPlatform";

const props = defineProps({
  points: { type: Array, default: () => [] },
  reference: { type: Object, default: () => ({}) },
  unit: { type: String, default: "" },
  indicatorName: { type: String, default: "健康指标" },
  sourceName: { type: String, default: "" },
});
const appearance = useAppearanceStore();
const container = ref(null);
let chart;
let resizeObserver;

const ariaLabel = computed(() => `${props.indicatorName}，${props.sourceName}，共 ${props.points.length} 次记录；首末值和需关注值直接标注，其余数值可悬浮查看`);

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

function render() {
  if (!chart) return;
  const dates = props.points.map((point) => formatBusinessDate(point.date, { short: true }));
  const values = props.points.map((point) => ({ value: Number(point.value), point }));
  const referenceLines = [];
  if (Number.isFinite(props.reference?.low)) referenceLines.push({ yAxis: props.reference.low, name: `下限 ${props.reference.low}` });
  if (Number.isFinite(props.reference?.high)) referenceLines.push({ yAxis: props.reference.high, name: `上限 ${props.reference.high}` });
  const option = buildTrendChartOption({
    theme: appearance.effectiveTheme,
    careMode: appearance.careMode,
    xAxisData: dates,
    yAxisData: values,
    unit: props.unit,
    referenceLines,
  });
  option.grid.bottom = props.points.length > 8 ? (appearance.careMode ? 72 : 62) : option.grid.bottom;
  option.tooltip.formatter = (items) => {
    const item = Array.isArray(items) ? items[0] : items;
    const point = item?.data?.point || {};
    const originalReference = point.reference ? `<br>报告参考：${escapeHtml(point.reference)}` : "";
    const abnormal = point.is_abnormal === true ? "<br><strong>报告标记：需关注</strong>" : "";
    const source = point.source?.type === "self" ? "个人日常测量" : [point.source?.name, point.source?.branch_name].filter(Boolean).join(" · ") || props.sourceName;
    const other = point.same_day_other_count ? `<br>同日另有 ${point.same_day_other_count} 条机构记录，已采用最后归档结果` : "";
    return `${escapeHtml(point.date || "日期待核对")}<br>${escapeHtml(props.indicatorName)}：<strong>${escapeHtml(point.value)} ${escapeHtml(props.unit)}</strong><br>来源：${escapeHtml(source)}${originalReference}${abnormal}${other}`;
  };
  option.series[0].label = {
    show: true,
    position: "top",
    distance: appearance.careMode ? 10 : 7,
    fontSize: appearance.careMode ? 16 : 13,
    color: appearance.effectiveTheme === "dark" ? "#f5f5f7" : "#1d1d1f",
    formatter: ({ value, dataIndex, data }) => {
      const showAll = props.points.length <= 4;
      const isEdge = dataIndex === 0 || dataIndex === props.points.length - 1;
      if (!showAll && !isEdge && data?.point?.is_abnormal !== true) return "";
      return `${Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}${props.unit ? ` ${props.unit}` : ""}`;
    },
  };
  option.series[0].labelLayout = { hideOverlap: true, moveOverlap: "shiftY" };
  if (Number.isFinite(props.reference?.low) && Number.isFinite(props.reference?.high)) {
    const referenceAppearance = option.__appearance;
    option.series[0].markArea = {
      silent: true,
      itemStyle: { color: referenceAppearance.referenceArea },
      data: [[{ yAxis: props.reference.low, name: props.reference.label || "参考范围" }, { yAxis: props.reference.high }]],
    };
  }
  if (props.points.length > 8) {
    option.dataZoom = [
      { type: "inside", startValue: Math.max(0, props.points.length - 8), endValue: props.points.length - 1 },
      { type: "slider", height: appearance.careMode ? 24 : 18, bottom: 8, startValue: Math.max(0, props.points.length - 8), endValue: props.points.length - 1 },
    ];
  }
  chart.setOption(option, true);
}

onMounted(async () => {
  await nextTick();
  chart = initTrendChart(container.value);
  resizeObserver = new ResizeObserver(() => chart?.resize());
  resizeObserver.observe(container.value);
  render();
});
watch(() => [props.points, props.reference, props.unit, appearance.effectiveTheme, appearance.careMode], render, { deep: true });
onBeforeUnmount(() => { resizeObserver?.disconnect(); chart?.dispose(); });
</script>

<style scoped>
.health-trend-chart { width: 100%; height: 310px; min-width: 0; }
:global(html[data-care="on"]) .health-trend-chart { height: 360px; }
</style>
