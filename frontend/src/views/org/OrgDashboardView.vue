<template>
  <div class="workspace-page org-dashboard" v-loading="loading">
    <section class="org-hero">
      <div>
        <p class="org-kicker">今日运营工作台</p>
        <h2>{{ summary.institution ? `${summary.institution.name} · ${summary.institution.branch_name}` : "机构运营总览" }}</h2>
        <p class="org-hero-copy">先处理到检与待归档任务，再检查服务容量和套餐审核进度。</p>
      </div>
      <div class="org-hero-actions">
        <el-button type="primary" @click="goReports('today')">开始今日接待</el-button>
        <el-button @click="router.push({ name: 'org-packages' })">管理体检服务</el-button>
      </div>
    </section>

    <section class="org-overview-grid" aria-label="今日运营指标">
      <article v-for="metric in metrics" :key="metric.label" class="org-overview-card">
        <span class="org-overview-icon" aria-hidden="true">{{ metric.icon }}</span>
        <div>
          <small>{{ metric.label }}</small>
          <strong>{{ metric.value }}</strong>
          <p>{{ metric.note }}</p>
        </div>
      </article>
    </section>

    <section class="org-task-layout">
      <article class="org-panel">
        <header class="org-panel-header">
          <div><p class="org-kicker">优先处理</p><h3>今日任务</h3></div>
          <el-button link type="primary" @click="goReports('all')">查看全部</el-button>
        </header>
        <div class="org-task-list">
          <button v-for="task in tasks" :key="task.title" type="button" class="org-task" @click="task.action">
            <span class="org-task-mark" :class="task.tone">{{ task.icon }}</span>
            <span><strong>{{ task.title }}</strong><small>{{ task.description }}</small></span>
            <b>{{ task.count }}</b>
            <i aria-hidden="true">›</i>
          </button>
        </div>
      </article>

      <article class="org-panel org-guide">
        <p class="org-kicker">规范提醒</p>
        <h3>健康数据归档流程</h3>
        <ol>
          <li><span>1</span><div><strong>确认受检者已到检</strong><small>确认后用户将不能再取消预约。</small></div></li>
          <li><span>2</span><div><strong>上传医生提交复核</strong><small>填写上传医生，并将完整报告转为待复核。</small></div></li>
          <li><span>3</span><div><strong>复核医生确认归档</strong><small>可先修正问题，确认后锁档并展示给用户。</small></div></li>
        </ol>
        <el-alert title="当前分院只能处理本院预约；可在机构共享档案中只读查看同机构其他分院已归档的体检报告。" type="info" show-icon :closable="false" />
      </article>
    </section>

    <section class="org-panel audience-panel" v-loading="insightLoading">
      <header class="org-panel-header">
        <div><p class="org-kicker">经营洞察</p><h3>用户人群画像与套餐分析</h3></div>
        <div class="audience-filters">
          <el-select v-model="insightFilters.scope" @change="loadInsights">
            <el-option label="当前分院" value="branch" />
            <el-option label="全部分院" value="organization" />
          </el-select>
          <el-select v-model="insightFilters.period_days" @change="loadInsights">
            <el-option label="近 30 天" :value="30" />
            <el-option label="近 90 天" :value="90" />
            <el-option label="近 12 个月" :value="365" />
            <el-option label="全部历史" :value="0" />
          </el-select>
        </div>
      </header>
      <p class="audience-note">仅使用聚合后的预约统计生成画像，不展示或发送任何个人健康档案。</p>
      <div class="audience-sample-summary">
        <el-tag effect="plain">去重受检者 {{ aggregate.unique_user_count || 0 }} 人</el-tag>
        <el-tag effect="plain">已完成体检 {{ aggregate.report_count || 0 }} 次</el-tag>
        <el-tag v-if="aggregate.period_start" effect="plain">{{ aggregate.period_start }} 至 {{ aggregate.period_end }}</el-tag>
        <el-tag v-else effect="plain">全部历史至 {{ aggregate.period_end || "当前" }}</el-tag>
        <el-tag v-if="aggregate.report_count > 0 && aggregate.unique_user_count < 10" type="warning">样本较少，结论仅供初步参考</el-tag>
      </div>
      <div v-if="insightHasData" class="audience-layout">
        <article class="audience-chart">
          <h4>性别分布</h4>
          <div v-for="item in genderBreakdown" :key="item.label" class="audience-bar">
            <span>{{ item.label }}</span><i><b :style="{ width: `${item.percent}%` }"></b></i><strong>{{ item.count }} 人</strong>
          </div>
        </article>
        <article class="audience-chart">
          <h4>年龄段分布</h4>
          <div v-for="item in ageBreakdown" :key="item.label" class="audience-bar">
            <span>{{ item.label }}</span><i><b :style="{ width: `${item.percent}%` }"></b></i><strong>{{ item.count }} 人</strong>
          </div>
        </article>
        <article class="audience-chart">
          <h4>热门套餐</h4>
          <div v-for="item in packageBreakdown" :key="item.label" class="audience-bar">
            <span>{{ item.label }}</span><i><b :style="{ width: `${item.percent}%` }"></b></i><strong>{{ item.count }} 次</strong>
          </div>
        </article>
        <article class="audience-ai-card">
          <span>AI 运营建议</span>
          <h4>{{ aiInsight.title || "根据当前人群优化服务供给" }}</h4>
          <p>{{ aiInsight.analysis_text || aiInsight.summary || aiInsight.analysis || "累计更多预约后，平台会给出更稳定的人群画像与套餐建议。" }}</p>
          <ul v-if="aiRecommendations.length">
            <li v-for="(item, index) in aiRecommendations" :key="index">{{ typeof item === "string" ? item : item.content || item.title }}</li>
          </ul>
        </article>
      </div>
      <el-empty v-else-if="!insightLoading" description="当前筛选范围内还没有足够的预约数据" :image-size="86" />
    </section>

    <section v-if="recentReviews.length" class="org-panel">
      <header class="org-panel-header"><div><p class="org-kicker">平台反馈</p><h3>最近的套餐审核</h3></div><el-button link type="primary" @click="router.push({name:'org-package-reviews'})">查看审核记录</el-button></header>
      <div class="review-strip"><article v-for="review in recentReviews" :key="review.id"><el-tag :type="reviewTone(review.status)" size="small">{{reviewLabel(review.status)}}</el-tag><strong>{{review.package_name || review.name || '套餐变更'}}</strong><span>{{review.reason || review.review_note || review.comment || '平台已更新审核状态，请查看详情。'}}</span></article></div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";

import { fetchOrgDashboard } from "../../api/dashboards";
import { fetchOrgAudienceInsights } from "../../api/org";

const router = useRouter();
const summary = ref({});
const loading = ref(false);
const insightLoading = ref(false);
const insights = ref({});
const insightFilters = reactive({ scope: "branch", period_days: 365 });

function normalizeBreakdown(source) {
  if (!source) return [];
  const rows = Array.isArray(source)
    ? source
    : Object.entries(source).map(([label, value]) => (
      typeof value === "object" ? { label, ...value } : { label, count: value }
    ));
  const counts = rows.map((item) => Number(item.count ?? item.value ?? item.total ?? 0));
  const total = counts.reduce((sum, value) => sum + value, 0);
  const max = Math.max(...counts, 1);
  return rows.map((item, index) => ({
    label: item.label || item.name || item.range || item.package_name || "其他",
    count: counts[index],
    percent: Number(item.percentage ?? item.percent ?? (total ? (counts[index] / total) * 100 : (counts[index] / max) * 100)),
  }));
}

const aggregate = computed(() => insights.value.aggregate || {});
const genderBreakdown = computed(() => normalizeBreakdown(
  aggregate.value.gender_distribution || aggregate.value.gender || aggregate.value.by_gender,
));
const ageBreakdown = computed(() => normalizeBreakdown(
  aggregate.value.age_distribution || aggregate.value.age_groups || aggregate.value.by_age,
));
const packageBreakdown = computed(() => normalizeBreakdown(
  aggregate.value.package_ranking || aggregate.value.package_distribution || aggregate.value.top_packages || aggregate.value.by_package,
).slice(0, 6));
const aiInsight = computed(() => insights.value.ai || {});
const aiRecommendations = computed(() => aiInsight.value.recommendations || aiInsight.value.suggestions || []);
const insightHasData = computed(() => Number(aggregate.value.report_count || 0) > 0);

const appointmentCounts = computed(() => summary.value.appointment_status_counts || {});
const today = computed(() => summary.value.today || {});
const recentReviews = computed(() => summary.value.recent_package_reviews || []);
const metrics = computed(() => [
  { label: "今日已预约", value: today.value.booked ?? appointmentCounts.value.unfulfilled ?? 0, icon: "约", note: today.value.capacity ? `接待能力 ${today.value.capacity} 人` : "今日服务安排" },
  { label: "等待到检", value: today.value.awaiting_arrival ?? appointmentCounts.value.unfulfilled ?? 0, icon: "到", note: "需要核对身份并接待" },
  { label: "等待归档", value: today.value.awaiting_archive ?? appointmentCounts.value.awaiting_report ?? 0, icon: "档", note: "需要完成结果复核" },
  { label: "今日剩余名额", value: today.value.remaining ?? "不限", icon: "余", note: `${today.value.waitlist_subscriptions || 0} 人订阅空位提醒` },
]);
const tasks = computed(() => [
  { title: "确认今日到检", description: "核对身份并开始本次体检", count: today.value.awaiting_arrival ?? appointmentCounts.value.unfulfilled ?? 0, icon: "到", tone: "is-green", action: () => goReports("today") },
  { title: "完成待归档数据", description: "补充结果、检查影像并提交", count: today.value.awaiting_archive ?? appointmentCounts.value.awaiting_report ?? 0, icon: "档", tone: "is-orange", action: () => goReports("archive") },
  { title: "查看套餐审核反馈", description: "处理待审核或被退回的服务变更", count: summary.value.pending_package_review_count || 0, icon: "审", tone: "is-blue", action: () => router.push({ name: "org-package-reviews" }) },
]);
const reviewLabel=(status)=>({pending:"待审核",approved:"已通过",rejected:"需调整"}[status]||"已更新");
const reviewTone=(status)=>({pending:"warning",approved:"success",rejected:"danger"}[status]||"info");

function goReports(view) {
  router.push({ name: "org-reports", query: { view } });
}

async function loadInsights() {
  insightLoading.value = true;
  try {
    const { data } = await fetchOrgAudienceInsights({ ...insightFilters });
    insights.value = data || {};
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "人群画像加载失败");
    insights.value = {};
  } finally {
    insightLoading.value = false;
  }
}

onMounted(async () => {
  loading.value = true;
  try {
    const [dashboardResponse] = await Promise.all([fetchOrgDashboard(), loadInsights()]);
    summary.value = dashboardResponse.data.summary || {};
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "运营数据加载失败");
  } finally {
    loading.value = false;
  }
});
</script>

<style scoped>
.org-dashboard { display: grid; gap: 18px; }
.org-hero { display:flex; align-items:center; justify-content:space-between; gap:24px; padding:28px; border:1px solid #dce8e6; border-radius:18px; background:linear-gradient(135deg,#f1faf7,#fff 72%); }
.org-hero h2,.org-panel h3 { margin:4px 0 8px; color:#173f42; }
.org-hero h2 { font-size:clamp(24px,3vw,34px); }
.org-kicker { margin:0; color:var(--workspace-accent); font-size:12px; font-weight:800; letter-spacing:.08em; }
.org-hero-copy { margin:0; color:#60777a; line-height:1.7; }
.org-hero-actions { display:flex; gap:10px; flex-wrap:wrap; }
.org-overview-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; }
.org-overview-card { display:flex; gap:14px; padding:20px; border:1px solid #e0e9e7; border-radius:16px; background:#fff; }
.org-overview-icon,.org-task-mark { display:grid; place-items:center; flex:0 0 auto; border-radius:12px; color:#0a6d61; background:#e7f5f1; font-weight:800; }
.org-overview-icon { width:42px; height:42px; }
.org-overview-card small,.org-task small,.org-guide small { display:block; color:#758789; }
.org-overview-card strong { display:block; margin:4px 0; color:#173f42; font-size:26px; }
.org-overview-card p { margin:0; color:#758789; font-size:12px; }
.org-task-layout { display:grid; grid-template-columns:minmax(0,1.35fr) minmax(320px,.65fr); gap:18px; }
.org-panel { padding:22px; border:1px solid #e0e9e7; border-radius:16px; background:#fff; }
.org-panel-header { display:flex; align-items:center; justify-content:space-between; gap:12px; }
.org-task-list { display:grid; gap:10px; margin-top:12px; }
.org-task { display:grid; grid-template-columns:auto minmax(0,1fr) auto auto; align-items:center; gap:13px; width:100%; padding:14px; border:1px solid #e5eceb; border-radius:13px; color:#27484b; background:#fbfdfc; cursor:pointer; text-align:left; }
.org-task:hover { border-color:#8fc8bd; background:#f3faf8; }
.org-task-mark { width:38px; height:38px; }
.org-task-mark.is-orange { color:#986020; background:#fff2dd; }
.org-task-mark.is-blue { color:#356b9a; background:#eaf3fb; }
.org-task b { font-size:22px; }.org-task i { color:#789; font-size:24px; font-style:normal; }
.org-guide ol { display:grid; gap:15px; padding:0; list-style:none; }
.org-guide li { display:flex; gap:12px; }.org-guide li>span { display:grid; place-items:center; width:28px; height:28px; border-radius:50%; color:white; background:#2c8c7c; font-weight:800; }
.org-guide :deep(.el-alert) { margin-top:18px; }
.review-strip{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:12px}.review-strip article{display:grid;align-content:start;gap:7px;padding:14px;border:1px solid #e5eceb;border-radius:12px;background:#fbfdfc}.review-strip :deep(.el-tag){width:max-content}.review-strip strong{color:#2b4c4f}.review-strip span{color:#738587;font-size:12px;line-height:1.55}
.audience-filters{display:flex;gap:8px}.audience-filters :deep(.el-select){width:130px}.audience-note{margin:0 0 12px;color:#718486;font-size:13px}.audience-sample-summary{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}.audience-layout{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.audience-chart,.audience-ai-card{padding:16px;border:1px solid #e4ecea;border-radius:14px;background:#fbfdfc}.audience-chart h4,.audience-ai-card h4{margin:0 0 14px;color:#294c4f}.audience-bar{display:grid;grid-template-columns:minmax(58px,.7fr) minmax(70px,1.2fr) auto;align-items:center;gap:8px;margin-top:10px;font-size:12px}.audience-bar>span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.audience-bar i{height:8px;border-radius:99px;background:#e4efec;overflow:hidden}.audience-bar b{display:block;height:100%;border-radius:inherit;background:linear-gradient(90deg,#55ad9d,#2e8174)}.audience-bar strong{color:#456062;font-size:12px}.audience-ai-card{grid-column:1/-1;border-color:#bcdcd5;background:linear-gradient(135deg,#edf8f5,#f9fcfb)}.audience-ai-card>span{color:#217769;font-size:12px;font-weight:800}.audience-ai-card p{color:#506a6c;line-height:1.7}.audience-ai-card ul{display:grid;gap:6px;margin:0;padding-left:20px;color:#385b5b}
@media(max-width:1100px){.org-overview-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.org-task-layout{grid-template-columns:1fr}}
@media(max-width:900px){.audience-layout{grid-template-columns:1fr}.audience-ai-card{grid-column:auto}}
@media(max-width:800px){.review-strip{grid-template-columns:1fr}}
@media(max-width:650px){.org-hero{align-items:flex-start;flex-direction:column;padding:20px}.org-hero-actions{width:100%}.org-hero-actions :deep(.el-button){flex:1}.org-overview-grid{grid-template-columns:1fr}.org-task{grid-template-columns:auto minmax(0,1fr) auto}.org-task i{display:none}.org-panel-header{align-items:flex-start;flex-direction:column}.audience-filters{width:100%}.audience-filters :deep(.el-select){width:100%}}
:global(html[data-theme="dark"]) .org-hero,
:global(html[data-theme="dark"]) .org-overview-card,
:global(html[data-theme="dark"]) .org-panel,
:global(html[data-theme="dark"]) .org-task,
:global(html[data-theme="dark"]) .review-strip article{border-color:var(--color-border);color:var(--color-text);background:var(--color-surface)}
:global(html[data-theme="dark"]) .org-hero h2,
:global(html[data-theme="dark"]) .org-panel h3,
:global(html[data-theme="dark"]) .org-overview-card strong,
:global(html[data-theme="dark"]) .org-task{color:var(--color-text)}
</style>
