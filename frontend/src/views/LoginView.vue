<template>
  <div class="auth-page">
    <header class="auth-header">
      <router-link class="auth-brand" to="/">
        <span>H</span><strong>康康健健 HealthDoc</strong>
      </router-link>
      <AppearanceQuickControls />
    </header>

    <main id="main-content" class="auth-main" tabindex="-1">
      <div class="auth-panel">
        <div class="auth-heading">
          <p>安全登录</p>
          <h1>欢迎回来</h1>
          <span>进入你的健康工作台，继续管理档案与长期趋势。</span>
        </div>
        <el-form :model="form" label-position="top" @submit.prevent="onSubmit">
          <el-form-item label="用户名">
            <el-input v-model="form.username" size="large" placeholder="请输入用户名" autocomplete="username" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input v-model="form.password" size="large" type="password" show-password placeholder="请输入密码" autocomplete="current-password" />
            <div style="width:100%;text-align:right;margin-top:6px"><router-link to="/forgot-password">忘记密码？</router-link></div>
          </el-form-item>
          <el-form-item label="图片验证码">
            <div class="captcha-row">
              <el-input v-model="form.captcha_answer" size="large" maxlength="4" placeholder="输入验证码" autocomplete="off" />
              <button
                class="captcha-image-button"
                type="button"
                :disabled="captchaLoading"
                aria-label="刷新图片验证码"
                aria-describedby="login-captcha-hint"
                @click="refreshCaptcha"
              >
                <img v-if="captchaImage" :src="captchaImage" alt="图片验证码" />
                <span v-else>加载中</span>
              </button>
              <small id="login-captcha-hint" class="captcha-refresh-hint">看不清？点击图片即可刷新</small>
            </div>
          </el-form-item>
          <el-alert v-if="errorMessage" :title="errorMessage" type="error" :closable="false" show-icon class="auth-alert" />
          <el-button native-type="submit" type="primary" size="large" :loading="loading" :disabled="loading" class="auth-submit">登录并进入工作台</el-button>
        </el-form>
        <p class="auth-switch">还没有账号？<router-link to="/register">立即注册</router-link></p>
        <router-link class="auth-back" to="/">← 返回公开门户</router-link>
      </div>
    </main>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { fetchCaptcha } from "../api/auth";
import AppearanceQuickControls from "../components/AppearanceQuickControls.vue";
import { useAuthStore } from "../stores/auth";
import { dashboardRouteForRole } from "../utils/roles";

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const form = reactive({ username: "", password: "", captcha_id: "", captcha_answer: "" });
const loading = ref(false);
const captchaLoading = ref(false);
const captchaImage = ref("");
const errorMessage = ref("");

async function loadCaptcha({ showError = true } = {}) {
  captchaLoading.value = true;
  try {
    const { data } = await fetchCaptcha();
    form.captcha_id = data.captcha_id;
    form.captcha_answer = "";
    captchaImage.value = data.image;
  } catch {
    form.captcha_id = "";
    captchaImage.value = "";
    if (showError) errorMessage.value = "验证码加载失败，请稍后刷新";
  } finally {
    captchaLoading.value = false;
  }
}

const refreshCaptcha = () => {
  errorMessage.value = "";
  return loadCaptcha();
};

async function onSubmit() {
  if (loading.value) return;
  if (!form.username.trim() || !form.password || !form.captcha_answer.trim()) {
    errorMessage.value = "请输入用户名、密码和验证码";
    return;
  }
  if (!form.captcha_id) {
    errorMessage.value = "验证码尚未加载完成";
    return;
  }
  loading.value = true;
  errorMessage.value = "";
  try {
    await authStore.loginUser({ ...form, username: form.username.trim() });
    const user = await authStore.fetchMe();
    const requestedRedirect = typeof route.query.redirect === "string" ? route.query.redirect : "";
    const resolvedRedirect = requestedRedirect.startsWith("/") && !requestedRedirect.startsWith("//")
      ? router.resolve(requestedRedirect)
      : null;
    const safeRedirect = resolvedRedirect?.meta?.requiresAuth
      && resolvedRedirect.meta.roles?.includes(user.role)
      ? requestedRedirect
      : null;
    await router.replace(safeRedirect || dashboardRouteForRole(user.role));
  } catch (error) {
    const code = error?.response?.data?.code;
    const rawMessage = error?.response?.data?.message;
    errorMessage.value = code === "INVALID_CAPTCHA" || rawMessage === "invalid captcha"
      ? "验证码不正确，请重新输入"
      : rawMessage || "登录失败，请检查账号信息";
    await loadCaptcha({ showError: false });
  } finally {
    loading.value = false;
  }
}

onMounted(loadCaptcha);
</script>
