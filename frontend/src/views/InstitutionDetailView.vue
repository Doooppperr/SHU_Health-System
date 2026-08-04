<template>
  <div class="workspace-page user-platform-page institution-profile-page">
    <el-button class="institution-back-button" text @click="router.push({ name: publicMode ? 'public-institutions' : 'institutions' })">← 返回机构列表</el-button>

    <el-alert v-if="errorMessage" :title="errorMessage" type="error" :closable="false" show-icon />
    <el-skeleton v-if="loading" :rows="8" animated />

    <template v-else-if="institution">
      <section class="institution-profile-hero">
        <InstitutionCoverImage :institution="institution" variant="detail" />
        <div class="institution-profile-hero__overlay"></div>
        <div class="institution-profile-hero__content">
          <el-tag effect="dark" type="success">可在线预约</el-tag>
          <h2>{{ institution.name }}</h2>
          <p>{{ institution.branch_name }} · {{ institution.district }}</p>
          <div class="institution-profile-hero__actions">
            <el-button type="primary" size="large" @click="scrollToPackages">查看体检套餐</el-button>
            <el-button v-if="institution.consult_phone" size="large" tag="a" :href="phoneHref">电话咨询</el-button>
          </div>
        </div>
      </section>

      <section class="institution-info-grid">
        <article class="institution-info-card"><span>地址</span><strong>{{ institution.address }}</strong><p>{{ institution.metro_info || "可在预约后向机构确认交通路线" }}</p></article>
        <article class="institution-info-card"><span>咨询电话</span><strong>{{ institution.consult_phone || "暂未提供" }}<template v-if="institution.ext"> 转 {{ institution.ext }}</template></strong><p>建议工作时间内联系</p></article>
        <article class="institution-info-card"><span>服务安排</span><strong>{{ institution.closed_day ? `${institution.closed_day}休息` : "以预约日期为准" }}</strong><p>{{ packages.length }} 个套餐可供选择</p></article>
      </section>

      <el-card shadow="never" class="user-panel institution-about-card">
        <template #header><div class="user-section-heading"><div><span>关于机构</span><h3>服务介绍</h3></div></div></template>
        <p>{{ institution.description || "该机构提供规范的健康体检与检查结果整理服务。" }}</p>
      </el-card>

      <section ref="packageSection" class="institution-package-section">
        <div class="user-section-heading user-section-heading--outside">
          <div><span>体检服务</span><h3>选择适合自己的套餐</h3></div>
          <small>价格与服务内容以当前页面为准</small>
        </div>
        <div class="institution-package-grid">
          <article v-for="pkg in packages" :key="pkg.id" class="institution-package-card">
            <header>
              <el-tag effect="plain">{{ packageTypeLabel(pkg.package_type) }}</el-tag>
              <span>{{ pkg.audience || genderLabel(pkg.gender_scope) }}</span>
            </header>
            <h4>{{ pkg.name }}</h4>
            <p>{{ pkg.description || pkg.focus_area }}</p>
            <div class="journey-domain-list"><span v-for="domain in pkg.domains || []" :key="domain.id">{{ domain.name }}</span></div>
            <dl>
              <div><dt>重点关注</dt><dd>{{ pkg.focus_area }}</dd></div>
              <div><dt>检查前须知</dt><dd>{{ pkg.booking_notice || "预约后请按机构通知做好检查准备。" }}</dd></div>
            </dl>
            <footer>
              <div><small>套餐价格</small><strong>¥ {{ Number(pkg.price || 0).toFixed(0) }}</strong></div>
              <el-button type="primary" @click="bookPackage(pkg)">{{ publicMode ? "登录后预约" : "选择套餐" }}</el-button>
            </footer>
          </article>
          <div v-if="!packages.length" class="user-empty-state user-empty-state--page">
            <span class="user-empty-state__icon">套</span>
            <div><strong>机构正在准备新的体检套餐</strong><p>可以稍后再来看看，或先电话咨询。</p></div>
          </div>
        </div>
      </section>

      <el-card shadow="never" class="user-panel institution-review-panel">
        <template #header><div class="user-section-heading"><div><span>到检体验</span><h3>用户评价</h3></div><small>{{ comments.length }} 条公开评价</small></div></template>
        <div class="institution-review-layout">
          <el-alert
            v-if="!publicMode && commentSanctionActive"
            :title="`评论权限已封禁：${commentSanction?.reason || '违反平台评论规范'}`"
            type="error"
            show-icon
            :closable="false"
            style="margin-bottom: 16px"
          />
          <form v-if="!publicMode && !commentSanctionActive" class="institution-review-form" @submit.prevent="submitComment">
            <h4>分享这次体验</h4>
            <p>在该机构完成体检并收到健康资料后，可以提交真实评价。</p>
            <el-rate v-model="commentForm.rating" :max="5" />
            <el-input v-model="commentForm.content" type="textarea" :rows="4" maxlength="1000" show-word-limit placeholder="例如：接待是否清楚、流程是否顺畅、现场感受如何……" />
            <el-button type="primary" native-type="submit" :loading="commentSubmitting">提交评价</el-button>
          </form>
          <div v-else class="institution-review-form">
            <h4>真实体验，登录后分享</h4>
            <p>访客可以浏览已经公开的评价；完成体检并收到健康数据后即可登录评价。</p>
            <el-button type="primary" plain @click="router.push({name:'login'})">登录平台</el-button>
          </div>
          <div class="institution-review-list">
            <article v-for="comment in comments" :key="comment.id" class="institution-review-card">
              <header><span class="institution-review-card__avatar">{{ (comment.author_display_name || comment.user?.username || "用").slice(0, 1).toUpperCase() }}</span><div><strong>{{ comment.author_display_name || comment.user?.username || "用户" }}</strong><el-rate :model-value="comment.rating" disabled /></div><time>{{ formatDate(comment.created_at) }}</time></header>
              <p>{{ comment.content }}</p>
              <div v-if="comment.reply" class="institution-review-reply"><strong>机构回复</strong><p>{{ comment.reply.content }}</p></div>
            </article>
            <el-empty v-if="!comments.length" description="还没有公开评价" :image-size="80" />
            <el-pagination v-if="commentsPagination.total>commentsPagination.page_size" v-model:current-page="commentsPagination.page" :page-size="commentsPagination.page_size" :total="commentsPagination.total" layout="prev, pager, next" @current-change="loadComments"/>
          </div>
        </div>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { useRoute, useRouter } from "vue-router";
import InstitutionCoverImage from "../components/InstitutionCoverImage.vue";
import { createInstitutionComment, fetchInstitutionComments, fetchMyCommentSanction } from "../api/comments";
import { fetchInstitutionDetail, fetchInstitutionPackages } from "../api/institutions";
import {
  fetchPublicInstitution,
  fetchPublicInstitutionComments,
  fetchPublicInstitutionPackages,
} from "../api/public";
import { formatDate, genderLabel, packageTypeLabel } from "../utils/userPlatform";

const route = useRoute();
const router = useRouter();
const props = defineProps({ publicMode: { type: Boolean, default: false } });
const publicMode = computed(() => props.publicMode);
const loading = ref(false);
const errorMessage = ref("");
const institution = ref(null);
const packages = ref([]);
const comments = ref([]);
const commentsPagination = reactive({ page: 1, page_size: 15, total: 0, pages: 0 });
const packageSection = ref(null);
const commentSubmitting = ref(false);
const commentSanction = ref(null);
const commentSanctionActive = computed(() => Boolean(
  commentSanction.value?.status === "active"
  || commentSanction.value?.is_active
  || commentSanction.value?.active,
));
const commentForm = reactive({ rating: 5, content: "" });
const phoneHref = computed(() => `tel:${String(institution.value?.consult_phone || "").replace(/[^\d+]/g, "")}`);

async function loadComments() {
  try {
    const request = publicMode.value ? fetchPublicInstitutionComments : fetchInstitutionComments;
    const { data } = await request(route.params.id, { page: commentsPagination.page, page_size: 15 });
    comments.value = data.items || [];
    Object.assign(commentsPagination, data.pagination || {});
  } catch (error) {
    if (publicMode.value && [401, 404].includes(error?.response?.status)) {
      comments.value = [];
      Object.assign(commentsPagination, { page: 1, page_size: 15, total: 0, pages: 0 });
      return;
    }
    throw error;
  }
}

async function loadCommentSanction() {
  if (props.publicMode) return;
  try {
    const { data } = await fetchMyCommentSanction();
    commentSanction.value = data.item || data.sanction || data || null;
  } catch (error) {
    if (error?.response?.status !== 404) throw error;
  }
}

async function loadData() {
  loading.value = true;
  errorMessage.value = "";
  try {
    const [detailResponse, packageResponse] = await Promise.all([
      publicMode.value ? fetchPublicInstitution(route.params.id) : fetchInstitutionDetail(route.params.id),
      publicMode.value ? fetchPublicInstitutionPackages(route.params.id) : fetchInstitutionPackages(route.params.id),
    ]);
    institution.value = detailResponse.data.item;
    packages.value = packageResponse.data.items || [];
    await Promise.all([loadComments(), loadCommentSanction()]);
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "机构信息暂时没有加载成功，请稍后重试";
    institution.value = null;
    packages.value = [];
    comments.value = [];
  } finally {
    loading.value = false;
  }
}

function scrollToPackages() {
  packageSection.value?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function bookPackage(pkg) {
  if (publicMode.value) {
    const bookingQuery = {
      institution_id: String(institution.value.id),
      package_id: String(pkg.id),
    };
    if (typeof route.query.appointment_date === "string") {
      bookingQuery.appointment_date = route.query.appointment_date;
    }
    const redirect = router.resolve({ name: "appointments", query: bookingQuery }).fullPath;
    router.push({ name: "login", query: { redirect } });
    return;
  }
  router.push({ name: "appointments", query: { institution_id: institution.value.id, package_id: pkg.id } });
}

async function submitComment() {
  if (!commentForm.content.trim()) {
    ElMessage.warning("请先写下这次体检的真实感受");
    return;
  }
  commentSubmitting.value = true;
  try {
    await createInstitutionComment({
      institution_id: Number(route.params.id),
      content: commentForm.content.trim(),
      rating: commentForm.rating,
    });
    commentForm.content = "";
    commentForm.rating = 5;
    ElMessage.success("评价已提交，通过审核后会公开展示");
  } catch (error) {
    const code = error?.response?.data?.code;
    const message = error?.response?.data?.message || "";
    if (["COMMENT_BANNED", "COMMENT_SANCTION_ACTIVE"].includes(code)) {
      commentSanction.value = error?.response?.data?.sanction || { status: "active", reason: message };
      ElMessage.error("你的评论权限已被封禁，请到“我的评论”查看原因并申诉");
    } else if (code === "comment_requires_record" || message.includes("upload a record")) {
      ElMessage.error("在该机构完成体检并收到健康资料后，才能提交评价");
    } else {
      ElMessage.error(message || "评价提交失败，请稍后重试");
    }
  } finally {
    commentSubmitting.value = false;
  }
}

watch(() => route.params.id, loadData);
onMounted(loadData);
</script>
