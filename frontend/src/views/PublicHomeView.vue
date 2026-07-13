<template>
  <div class="portal-page">
    <header class="portal-header">
      <router-link class="portal-brand" to="/">
        <span>H</span>
        <strong>康康健健 HealthDoc</strong>
      </router-link>
      <nav class="portal-nav" aria-label="公开页面导航">
        <a href="#features">核心能力</a>
        <a href="#process">使用流程</a>
        <a href="#privacy">隐私保护</a>
        <a href="#about">关于我们</a>
      </nav>
      <div class="portal-actions">
        <AppearanceQuickControls />
        <template v-if="authStore.accessToken && authStore.user">
          <el-button type="primary" round @click="enterWorkspace"><span class="portal-action-full">进入工作台</span><span class="portal-action-short">工作台</span></el-button>
        </template>
        <template v-else>
          <el-button round @click="router.push({ name: 'login' })">登录</el-button>
          <el-button type="primary" round @click="router.push({ name: 'register' })"><span class="portal-action-full">免费注册</span><span class="portal-action-short">注册</span></el-button>
        </template>
      </div>
    </header>

    <main id="main-content" tabindex="-1">
      <section class="portal-hero">
        <div class="portal-hero-copy">
          <p class="portal-kicker">个人健康数据，清晰可见、安全可控</p>
          <h1>让每一次体检，<br /><span>都成为长期健康的线索</span></h1>
          <p class="portal-lead">
            集中管理本人和亲友的健康档案，通过 OCR 快速录入、趋势追踪和分级授权，
            在个人、体检机构与平台之间建立清晰的数据边界。
          </p>
          <div class="portal-hero-actions">
            <el-button type="primary" size="large" round @click="primaryAction">
              {{ authStore.accessToken ? "进入我的工作台" : "开始建立健康档案" }}
            </el-button>
            <a href="#features">了解系统能力 <span>→</span></a>
          </div>
          <div class="portal-trust-row">
            <span>✓ 档案分级授权</span>
            <span>✓ 报告鉴权访问</span>
            <span>✓ 三角色权限隔离</span>
          </div>
        </div>

        <div class="portal-product-stage" aria-label="健康数据产品界面示意">
          <div class="portal-health-card portal-health-card--main">
            <div class="portal-health-card-head">
              <span class="portal-mini-mark">H</span>
              <div><strong>健康趋势概览</strong><small>持续追踪关键指标</small></div>
              <span class="portal-live-dot">已更新</span>
            </div>
            <div class="portal-chart">
              <span v-for="height in chartBars" :key="height" :style="{ height: `${height}%` }" />
            </div>
            <div class="portal-metric-row">
              <div><small>已确认档案</small><strong>12</strong></div>
              <div><small>跟踪指标</small><strong>28</strong></div>
              <div><small>授权亲友</small><strong>3</strong></div>
            </div>
          </div>
          <div class="portal-product-notes" aria-label="产品能力摘要">
            <span><b>OCR</b> 报告识别后由用户确认</span>
            <span><b>趋势</b> 同口径指标持续追踪</span>
            <span><b>隐私</b> 数据开放边界清晰</span>
          </div>
        </div>
      </section>

      <section id="features" class="portal-section portal-features">
        <div class="portal-section-heading">
          <p>CORE CAPABILITIES</p>
          <h2>围绕健康档案的一站式服务</h2>
          <span>从录入、确认到长期趋势，以更少操作沉淀更可靠的数据。</span>
        </div>
        <div class="portal-feature-grid">
          <article v-for="feature in features" :key="feature.title">
            <span class="portal-feature-icon">{{ feature.icon }}</span>
            <h3>{{ feature.title }}</h3>
            <p>{{ feature.description }}</p>
            <small>{{ feature.note }}</small>
          </article>
        </div>
      </section>

      <section id="process" class="portal-section portal-process">
        <div class="portal-section-heading portal-section-heading--light">
          <p>SIMPLE PROCESS</p>
          <h2>三步建立连续健康视图</h2>
        </div>
        <div class="portal-step-grid">
          <article v-for="(step, index) in steps" :key="step.title">
            <span>0{{ index + 1 }}</span>
            <h3>{{ step.title }}</h3>
            <p>{{ step.description }}</p>
          </article>
        </div>
      </section>

      <section id="privacy" class="portal-section portal-privacy">
        <div>
          <p class="portal-kicker">PRIVACY BY DESIGN</p>
          <h2>数据属于你，开放范围由你决定</h2>
          <p>
            未关联机构的档案只服务于个人健康管理；关联机构的已确认标准化数据，
            才会向对应机构管理员只读开放。联系方式和原始报告不在开放范围内。
          </p>
        </div>
        <div class="portal-privacy-list">
          <div><span>01</span><p><strong>角色隔离</strong><small>用户、机构管理员与系统管理员拥有独立工作台和接口。</small></p></div>
          <div><span>02</span><p><strong>最小开放</strong><small>机构仅查看来源于本机构且已确认的结构化健康数据。</small></p></div>
          <div><span>03</span><p><strong>报告保护</strong><small>健康报告通过身份鉴权访问，不作为公共文件暴露。</small></p></div>
        </div>
      </section>

      <section id="about" class="portal-section portal-about">
        <div>
          <p class="portal-kicker">ABOUT HEALTHDOC</p>
          <h2>用可靠的数据连接每一段健康旅程</h2>
        </div>
        <p>
          康康健健是面向个人健康档案管理的课程实践项目。我们重视易用性，也坚持权限边界、
          数据可追溯和健康建议的谨慎表达，让技术真正服务于长期健康管理。
        </p>
      </section>
    </main>

    <footer class="portal-footer">
      <span>© 2026 康康健健 HealthDoc</span>
      <span>健康数据管理 · 隐私优先</span>
    </footer>
  </div>
</template>

<script setup>
import { useRouter } from "vue-router";

import AppearanceQuickControls from "../components/AppearanceQuickControls.vue";
import { useAuthStore } from "../stores/auth";
import { dashboardRouteForRole } from "../utils/roles";

const router = useRouter();
const authStore = useAuthStore();
const chartBars = [34, 48, 42, 63, 56, 76, 68, 83, 74, 91];
const features = [
  { icon: "档", title: "健康档案", description: "统一管理本人及已授权亲友的体检记录与标准化指标。", note: "手工录入 · 状态确认" },
  { icon: "OCR", title: "报告智能识别", description: "上传 PDF 或图片，自动提取候选指标并由你核对后入档。", note: "先确认 · 后归档" },
  { icon: "趋", title: "指标趋势", description: "按归属人与指标查看时间变化，快速理解长期健康线索。", note: "同口径 · 可追踪" },
  { icon: "友", title: "亲友授权", description: "通过明确授权协助家人管理档案，授权可随时调整。", note: "可控授权 · 清晰边界" },
];
const steps = [
  { title: "创建或上传档案", description: "手工创建档案，或上传报告让 OCR 帮你提取候选数据。" },
  { title: "核对并确认指标", description: "检查归属人、来源与指标映射，确认后沉淀为正式档案。" },
  { title: "持续观察趋势", description: "结合历次体检查看趋势，在需要时向专业医生咨询。" },
];

function enterWorkspace() {
  router.push(dashboardRouteForRole(authStore.user?.role));
}

function primaryAction() {
  if (authStore.accessToken && authStore.user) {
    enterWorkspace();
    return;
  }
  router.push({ name: "register" });
}
</script>
