<template>
  <div class="portal-page">
    <header class="portal-header">
      <router-link class="portal-brand" to="/">
        <span>H</span>
        <strong>康康健健 HealthDoc</strong>
      </router-link>
      <nav class="portal-nav" aria-label="公开页面导航">
        <router-link :to="{name:'public-institutions'}">机构与套餐</router-link>
        <a href="#features">核心能力</a>
        <a href="#process">使用流程</a>
        <a href="#privacy">隐私保护</a>
        <a href="#about">关于我们</a>
        <a href="#join-us">加入我们</a>
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
            由体检机构上传并复核发布标准化报告，个人记录日常测量；关联亲友可在授权后直接切换账号，
            健康身份码只授予单次代预约资格。
          </p>
          <div class="portal-hero-actions">
            <el-button type="primary" size="large" round @click="primaryAction">
              {{ authStore.accessToken ? "进入我的工作台" : "开始建立健康视图" }}
            </el-button>
            <a href="#features">了解系统能力 <span>→</span></a>
            <router-link :to="{name:'public-institutions'}">先看看机构与套餐 <span>→</span></router-link>
          </div>
          <div class="portal-trust-row">
            <span>✓ 档案分级授权</span>
            <span>✓ 检查资料受控查看</span>
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
            <span><b>导入</b> 识别结果由机构人工确认</span>
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
            亲友关联经双方确认后，可在受控会话中切换进入对方账号；健康身份码临时添加只用于本次代预约，
            不授予账号切换、健康数据访问或亲友关系。机构仅处理自身业务，平台管理员不能查看健康档案。
          </p>
        </div>
        <div class="portal-privacy-list">
          <div><span>01</span><p><strong>角色隔离</strong><small>用户、机构管理员与系统管理员拥有独立工作台和接口。</small></p></div>
          <div><span>02</span><p><strong>最小开放</strong><small>机构只管理自己创建的报告，管理员不接触任何健康内容。</small></p></div>
          <div><span>03</span><p><strong>报告保护</strong><small>上传医生确认后进入待复核，复核发布并锁档后用户方可查看。</small></p></div>
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

      <section id="join-us" class="portal-section portal-join-us">
        <div>
          <p class="portal-kicker">JOIN HEALTHDOC</p>
          <h2>加入我们</h2>
          <p>机构入驻不开放在线注册，请通过以下方式联系平台，由系统管理员完成分院与机构账号创建。</p>
        </div>
        <address>
          <div><span>平台地址</span><strong>{{ contact.address }}</strong></div>
          <a :href="`tel:${contact.phone}`"><span>联系电话</span><strong>{{ contact.phone }}</strong></a>
          <a :href="`mailto:${contact.email}`"><span>联系邮箱</span><strong>{{ contact.email }}</strong></a>
        </address>
      </section>
    </main>

    <footer class="portal-footer">
      <span>© 2026 康康健健 HealthDoc</span>
      <span>健康数据管理 · 隐私优先 · {{ buildLabel() }}</span>
    </footer>
  </div>
</template>

<script setup>
import { onMounted, reactive } from "vue";
import { useRouter } from "vue-router";

import { fetchPublicContact } from "../api/public";
import AppearanceQuickControls from "../components/AppearanceQuickControls.vue";
import { useAuthStore } from "../stores/auth";
import { dashboardRouteForRole } from "../utils/roles";
import { buildLabel } from "../utils/buildInfo";

const router = useRouter();
const authStore = useAuthStore();
const contact = reactive({
  address: "上海市宝山区上大路99号",
  phone: "021-114514",
  email: "shucs666@shu.edu.cn",
});
const chartBars = [34, 48, 42, 63, 56, 76, 68, 83, 74, 91];
const features = [
  { icon: "线", title: "健康时间线", description: "一次体检一条记录，展开查看预约、到检与健康数据归档历程。", note: "业务日期 · 历程清晰" },
  { icon: "导", title: "机构健康数据归档", description: "机构可上传或录入体检报告；上传医生确认后进入待复核，由复核医生发布给用户。", note: "双人确认 · 发布锁档" },
  { icon: "趋", title: "健康趋势", description: "按健康领域查看多指标独立图表、机构来源分轨和检查附件事件。", note: "来源分轨 · 不造综合分" },
  { icon: "友", title: "关联账号", description: "双方确认后可直接切换关联账号；健康身份码仅用于临时代预约。", note: "受控切换 · 单次凭证" },
];
const steps = [
  { title: "上传并确认", description: "机构上传或录入标准化报告，由上传医生确认后进入待复核。" },
  { title: "复核并发布", description: "复核医生检查和修正报告，确认后发布给用户并永久锁档。" },
  { title: "记录并观察趋势", description: "记录日常自测，与机构数据一起形成每日有效趋势。" },
];

onMounted(async () => {
  try {
    const { data } = await fetchPublicContact();
    const item = data?.item || data;
    if (item?.address) contact.address = item.address;
    if (item?.phone) contact.phone = item.phone;
    if (item?.email) contact.email = item.email;
  } catch {
    // Keep the fixed platform contact as the visitor-safe fallback.
  }
});

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

<style scoped>
.portal-join-us{display:grid;grid-template-columns:minmax(0,.8fr) minmax(420px,1.2fr);gap:42px;align-items:start}
.portal-join-us h2{margin:8px 0 14px;font-size:clamp(28px,4vw,46px)}.portal-join-us p{color:var(--color-text-muted);line-height:1.8}
.portal-join-us address{display:grid;gap:12px;font-style:normal}.portal-join-us address a,.portal-join-us address>div{display:grid;gap:5px;padding:18px 20px;border:1px solid var(--color-border);border-radius:15px;color:inherit;background:var(--color-surface);text-decoration:none}.portal-join-us address a:hover{border-color:var(--color-primary,#17776b);transform:translateY(-1px)}.portal-join-us address span{color:var(--color-text-muted);font-size:12px}.portal-join-us address strong{font-size:17px}
@media(max-width:800px){.portal-join-us{grid-template-columns:1fr}}
</style>
