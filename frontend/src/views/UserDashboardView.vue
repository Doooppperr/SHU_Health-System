<template>
  <div class="workspace-page">
    <section class="welcome-panel"><div><p>统一健康视图</p><h2>今天也要照顾好自己</h2><span>机构提交的体检报告与日常自测自动汇入同一条时间线。</span></div><div class="welcome-actions"><el-button type="primary" @click="router.push({name:'measurements'})">记录日常测量</el-button></div></section>
    <el-alert v-if="errorMessage" :title="errorMessage" type="error" :closable="false" show-icon />
    <section class="metric-grid" v-loading="loading">
      <article v-for="item in metrics" :key="item.label" class="metric-card"><span class="metric-icon">{{ item.icon }}</span><div><small>{{ item.label }}</small><strong>{{ item.value }}</strong><p>{{ item.note }}</p></div></article>
    </section>
    <el-card shadow="never" class="dashboard-card"><template #header><div class="card-heading"><div><h3>最近健康事件</h3><p>时间线中的最新记录</p></div><el-button link type="primary" @click="router.push({name:'timeline'})">查看全部</el-button></div></template>
      <el-timeline v-if="events.length"><el-timeline-item v-for="event in events" :key="`${event.type}-${event.occurred_at}`" :timestamp="formatTime(event.occurred_at)"><strong>{{ event.title }}</strong></el-timeline-item></el-timeline><el-empty v-else description="暂无健康事件" />
    </el-card>
  </div>
</template>
<script setup>
import { computed, onMounted, ref } from "vue"; import { useRouter } from "vue-router"; import { fetchTimeline } from "../api/health";
const router=useRouter(),loading=ref(false),errorMessage=ref(""),events=ref([]);
const metrics=computed(()=>{const counts={self_measurement:0,institution_report:0,report_withdrawn:0};events.value.forEach(e=>{if(counts[e.type]!==undefined)counts[e.type]++});return[{label:"时间线事件",value:events.value.length,icon:"线",note:"统一健康记录"},{label:"日常测量",value:counts.self_measurement,icon:"测",note:"保留全部原始值"},{label:"机构报告",value:counts.institution_report,icon:"报",note:"机构提交后自动归档"},{label:"已撤下报告",value:counts.report_withdrawn,icon:"撤",note:"保留审计时间线"}]});
const formatTime=v=>v?new Date(v).toLocaleString("zh-CN",{hour12:false}):"-";
onMounted(async()=>{loading.value=true;try{const{data}=await fetchTimeline();events.value=(data.items||[]).slice(0,8)}catch(e){errorMessage.value=e?.response?.data?.message||"健康总览加载失败"}finally{loading.value=false}});
</script>
