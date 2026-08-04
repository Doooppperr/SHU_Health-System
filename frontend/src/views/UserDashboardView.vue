<template>
  <div class="workspace-page user-platform-page">
    <section class="user-hero">
      <div class="user-hero__copy">
        <span class="user-kicker">我的健康首页</span>
        <h2>{{ greeting }}，今天也照顾好自己</h2>
        <p>记录日常状态、安排体检，并在同一个地方查看每次检查留下的健康资料。</p>
        <div class="user-hero__actions">
          <el-button type="primary" size="large" @click="openMeasurement">记录今日测量</el-button>
          <el-button size="large" @click="openAppointments">预约体检</el-button>
        </div>
      </div>
      <div class="user-hero__today" aria-label="今日概览">
        <span>{{ todayLabel }}</span>
        <strong>{{ latestMeasurements.length ? `${latestMeasurements.length} 项近期指标` : "从一次测量开始" }}</strong>
        <small>{{ upcomingCount ? `${upcomingCount} 个体检安排正在进行` : "目前没有待完成的体检" }}</small>
      </div>
    </section>

    <el-alert v-if="errorMessage" :title="errorMessage" type="error" :closable="false" show-icon />

    <section class="user-dashboard-grid" v-loading="loading">
      <el-card shadow="never" class="user-panel user-dashboard-measurements">
        <template #header>
          <div class="user-section-heading">
            <div><span>日常状态</span><h3>最近测量</h3></div>
            <el-button link type="primary" @click="openMeasurement">再记一笔</el-button>
          </div>
        </template>
        <div v-if="latestMeasurements.length" class="latest-measurement-grid">
          <article v-for="item in latestMeasurements" :key="item.id" class="latest-measurement-card">
            <span>{{ item.indicator?.name || "健康指标" }}</span>
            <strong>{{ compactValue(item.value) }} <small>{{ item.unit }}</small></strong>
            <p>{{ relativeTime(item.measured_at) }}</p>
          </article>
        </div>
        <div v-else class="user-empty-state">
          <span class="user-empty-state__icon">测</span>
          <div><strong>今天还没有测量记录</strong><p>体重、心率、血氧等数据会在这里形成自己的变化轨迹。</p></div>
          <el-button type="primary" plain @click="openMeasurement">开始记录</el-button>
        </div>
      </el-card>

      <el-card shadow="never" class="user-panel user-dashboard-next">
        <template #header>
          <div class="user-section-heading">
            <div><span>下一步</span><h3>体检安排</h3></div>
            <el-button link type="primary" @click="openAppointments">管理预约</el-button>
          </div>
        </template>
        <article v-if="nextAppointment" class="next-appointment-card">
          <div class="next-appointment-card__date">
            <strong>{{ dayOfMonth(nextAppointment.appointment_date) }}</strong>
            <span>{{ monthLabel(nextAppointment.appointment_date) }}</span>
          </div>
          <div class="next-appointment-card__body">
            <el-tag :type="appointmentMeta(nextAppointment.status).type" effect="light">
              {{ appointmentMeta(nextAppointment.status).label }}
            </el-tag>
            <h4>{{ nextAppointment.package_name }}</h4>
            <p>{{ nextAppointment.institution?.name }} · {{ nextAppointment.institution?.branch_name }}</p>
          </div>
        </article>
        <div v-else class="user-empty-state user-empty-state--compact">
          <span class="user-empty-state__icon">约</span>
          <div><strong>给健康留一个固定时间</strong><p>选择适合自己的机构和套餐，提前安排一次体检。</p></div>
          <el-button type="primary" plain @click="openAppointments">去预约</el-button>
        </div>
      </el-card>
    </section>

    <el-card shadow="never" class="user-panel">
      <template #header>
        <div class="user-section-heading">
          <div><span>健康历程</span><h3>最近发生的事</h3></div>
          <el-button link type="primary" @click="router.push({ name: 'timeline' })">查看完整时间线</el-button>
        </div>
      </template>
      <div v-if="recentRecords.length" class="dashboard-journey-list">
        <button
          v-for="record in recentRecords"
          :key="record.key"
          type="button"
          class="dashboard-journey-item"
          @click="openRecord(record)"
        >
          <span class="dashboard-journey-item__marker">{{ record.kind === 'self' ? '记' : '检' }}</span>
          <span class="dashboard-journey-item__main">
            <strong>{{ recordTitle(record) }}</strong>
            <small>{{ recordSubtitle(record) }}</small>
          </span>
          <time>{{ formatDate(record.businessDate, { year: undefined }) }}</time>
          <span aria-hidden="true">›</span>
        </button>
      </div>
      <div v-else class="user-empty-state">
        <span class="user-empty-state__icon">历</span>
        <div><strong>健康历程会从这里开始</strong><p>日常记录和体检过程都会按日期整理，方便以后回看。</p></div>
      </div>
    </el-card>

    <MeasurementDrawer v-model="measurementVisible" @saved="load" @deleted="load" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import MeasurementDrawer from "../components/MeasurementDrawer.vue";
import { fetchHealthDashboard } from "../api/health";
import { useAuthStore } from "../stores/auth";
import { isBasicProfileComplete } from "../utils/v12";
import { ElMessage } from "element-plus";
import {
  appointmentMeta,
  formatDate,
  normalizeTimelineEntry,
} from "../utils/userPlatform";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const loading = ref(false);
const errorMessage = ref("");
const dashboard = ref({});
const records = ref([]);
const measurementVisible = ref(false);
const profileReady = computed(() => isBasicProfileComplete(auth.user || {}));

const greeting = computed(() => {
  const hour = new Date().getHours();
  if (hour < 11) return "早上好";
  if (hour < 14) return "中午好";
  if (hour < 18) return "下午好";
  return "晚上好";
});
const todayLabel = computed(() => new Date().toLocaleDateString("zh-CN", { month: "long", day: "numeric", weekday: "long" }));
const latestMeasurements = computed(() => (dashboard.value.today_measurements?.summary?.highlights || []).map((item) => ({
  ...item,
  id: `${item.indicator_id}-${item.measured_at}`,
  indicator_dict_id: item.indicator_id,
  indicator: { name: item.name },
})));
const normalizedNextAppointment = computed(() => dashboard.value.next_appointment
  ? normalizeTimelineEntry(dashboard.value.next_appointment)
  : null);
const nextAppointment = computed(() => normalizedNextAppointment.value?.item || null);
const upcomingCount = computed(() => nextAppointment.value ? 1 : 0);
const recentRecords = computed(() => records.value.slice(0, 6));

function compactValue(value) {
  const number = Number(value);
  return Number.isInteger(number) ? number : Number(number.toFixed(2));
}

function relativeTime(value) {
  const diff = Date.now() - new Date(value).getTime();
  if (!Number.isFinite(diff)) return "最近记录";
  const hours = Math.floor(diff / 3600000);
  if (hours < 1) return "刚刚记录";
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  return days < 7 ? `${days} 天前` : formatDate(value, { year: undefined });
}

function dayOfMonth(value) {
  return new Date(`${value}T00:00:00`).getDate();
}

function monthLabel(value) {
  return new Date(`${value}T00:00:00`).toLocaleDateString("zh-CN", { month: "short" });
}

function recordTitle(record) {
  if (record.kind === "self") return `${record.indicatorCount || 0} 项日常记录`;
  return record.item?.package_name || "体检安排";
}

function recordSubtitle(record) {
  if (record.kind === "self") return (record.domains || []).map((item) => item.name).join("、") || "本人记录";
  return [record.item?.institution?.name, appointmentMeta(record.item?.status).label].filter(Boolean).join(" · ");
}

function openRecord(record) {
  if (record.detailId) {
    router.push({ name: "health-data-detail", params: { id: record.detailId } });
  } else {
    router.push({ name: record.kind === "self" ? "health-data" : "timeline" });
  }
}

function requireProfile() {
  if (profileReady.value) return true;
  window.dispatchEvent(new CustomEvent("healthdoc-open-profile-gate"));
  ElMessage.warning("完成实名认证后才能使用该健康服务");
  return false;
}

function openMeasurement() {
  if (requireProfile()) measurementVisible.value = true;
}

function openAppointments() {
  if (requireProfile()) router.push({ name: "appointments" });
}

async function load() {
  loading.value = true;
  errorMessage.value = "";
  try {
    const { data } = await fetchHealthDashboard();
    dashboard.value = data || {};
    records.value = (data.recent_timeline || []).map(normalizeTimelineEntry);
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "首页内容暂时没有加载成功，请稍后重试";
  } finally {
    loading.value = false;
  }
}

watch(() => route.query.quick, (value) => {
  if (value === "measurement") openMeasurement();
});

watch(measurementVisible, (visible) => {
  if (!visible && route.query.quick === "measurement") {
    const query = { ...route.query };
    delete query.quick;
    router.replace({ query });
  }
});

onMounted(() => {
  load();
  if (route.query.quick === "measurement") openMeasurement();
});
</script>
