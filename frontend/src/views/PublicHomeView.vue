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
            由体检机构提交标准化报告，个人记录日常测量并授权亲友只读查看，
            在健康时间线中持续追踪趋势，同时保持清晰的数据边界。
          </p>
          <div class="portal-hero-actions">
            <el-button type="primary" size="large" round @click="primaryAction">
              {{ authStore.accessToken ? "进入我的工作台" : "开始建立健康视图" }}
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
              <div><small>机构报告</small><strong>12</strong></div>
              <div><small>跟踪指标</small><strong>28</strong></div>
              <div><small>授权亲友</small><strong>3</strong></div>
            </div>
          </div>
          <div class="portal-product-notes" aria-label="产品能力摘要">
            <span><b>OCR</b> 机构识别并人工复核</span>
            <span><b>趋势</b> 同口径指标持续追踪</span>
            <span><b>隐私</b> 数据开放边界清晰</span>
          </div>
        </div>
      </section>

      <section id="features" class="portal-section portal-features">
        <div class="portal-section-heading">
          <p>CORE CAPABILITIES</p>
          <h2>围绕健康时间线的一站式服务</h2>
          <span>机构体检与日常自测统一汇总，形成可追踪的长期趋势。</span>
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
            机构只生产本机构报告，不能浏览用户日常测量或其他健康信息；亲友只有在明确授权后，
            才能只读查看健康时间线。联系方式、健康身份码和个人资料始终隔离。
          </p>
        </div>
        <div class="portal-privacy-list">
          <div><span>01</span><p><strong>角色隔离</strong><small>用户、机构管理员与系统管理员拥有独立工作台和接口。</small></p></div>
          <div><span>02</span><p><strong>最小开放</strong><small>机构只管理自己创建的报告，管理员不接触任何健康内容。</small></p></div>
          <div><span>03</span><p><strong>报告保护</strong><small>用户只读查看标准化指标，OCR 原文件在机构锁定时删除。</small></p></div>
        </div>
      </section>

      <section id="about" class="portal-section portal-about">
        <div>
          <p class="portal-kicker">ABOUT HEALTHDOC</p>
          <h2>用可靠的数据连接每一段健康旅程</h2>
        </div>
        <p>
          康康健健是面向个人连续健康管理的课程实践项目。我们重视易用性，也坚持权限边界、
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
  { icon: "线", title: "健康时间线", description: "统一查看机构自动归档的体检报告和每一条日常测量。", note: "统一视图 · 来源清晰" },
  { icon: "OCR", title: "机构报告生产", description: "机构通过 OCR 或手工录入，复核锁定后自动归档给注册用户。", note: "机构负责 · 用户只读" },
  { icon: "趋", title: "指标趋势", description: "同日机构指标优先，缺失时采用用户当天最后一次自测。", note: "确定规则 · 原始值保留" },
  { icon: "友", title: "亲友授权", description: "授权亲友只读查看健康数据，个人资料始终隐藏。", note: "可控授权 · 清晰边界" },
];
const steps = [
  { title: "机构提交报告", description: "机构录入注册用户的姓名、健康身份码、日期和标准化指标。" },
  { title: "自动归档", description: "锁定报告提交后，系统按健康身份码和姓名自动归档到对应用户。" },
  { title: "记录并观察趋势", description: "记录日常自测，与机构数据一起形成每日有效趋势。" },
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
