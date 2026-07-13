<template>
  <div class="auth-page auth-page--register">
    <header class="auth-header">
      <router-link class="auth-brand" to="/"><span>H</span><strong>康康健健 HealthDoc</strong></router-link>
      <AppearanceQuickControls />
    </header>

    <main id="main-content" class="auth-main" tabindex="-1">
      <div class="auth-panel auth-panel--wide">
        <div class="auth-heading"><p>创建账号</p><h1>加入康康健健</h1><span>从一份档案开始，建立连续的健康视图。</span></div>

        <div class="register-mode" role="radiogroup" aria-label="注册身份">
          <label :class="{ active: mode === 'user' }">
            <input v-model="mode" type="radio" value="user" />
            <span><strong>普通用户</strong><small>管理个人与亲友健康档案</small></span>
          </label>
          <label :class="{ active: mode === 'staff' }">
            <input v-model="mode" type="radio" value="staff" />
            <span><strong>机构工作人员</strong><small>需要机构专属邀请码</small></span>
          </label>
        </div>

        <el-form :model="form" label-position="top" @submit.prevent="onSubmit">
          <div class="auth-form-grid">
            <el-form-item label="用户名" class="auth-form-full"><el-input v-model="form.username" size="large" placeholder="用于登录，注册后可由管理员维护" autocomplete="username" /></el-form-item>
            <el-form-item label="邮箱（可选）"><el-input v-model="form.email" size="large" placeholder="name@example.com" autocomplete="email" /></el-form-item>
            <el-form-item label="手机号（可选）"><el-input v-model="form.phone" size="large" placeholder="请输入手机号" autocomplete="tel" /></el-form-item>
            <el-form-item label="密码" class="auth-form-full"><el-input v-model="form.password" size="large" type="password" show-password placeholder="至少 6 位" autocomplete="new-password" /></el-form-item>
            <el-form-item v-if="mode === 'staff'" label="机构邀请码" class="auth-form-full">
              <el-input v-model="form.invite_code" size="large" placeholder="请输入系统管理员提供的一次性邀请码" />
              <p class="auth-field-tip">邀请码仅可使用一次，系统会自动绑定对应机构和机构管理员角色。</p>
            </el-form-item>
            <el-form-item label="图片验证码" class="auth-form-full">
              <div class="captcha-row">
                <el-input v-model="form.captcha_answer" size="large" maxlength="4" placeholder="输入验证码" autocomplete="off" @keyup.enter="onSubmit" />
                <button class="captcha-image-button" type="button" :disabled="captchaLoading" aria-label="刷新图片验证码" aria-describedby="register-captcha-hint" @click="refreshCaptcha"><img v-if="captchaImage" :src="captchaImage" alt="图片验证码" /><span v-else>加载中</span></button>
                <small id="register-captcha-hint" class="captcha-refresh-hint">看不清？点击图片即可刷新</small>
              </div>
            </el-form-item>
          </div>
          <el-alert v-if="mode === 'staff'" title="机构管理员只使用机构运营后台，不能进入个人健康、亲友授权或健康 AI 页面。" type="info" :closable="false" show-icon class="auth-alert" />
          <el-alert v-if="errorMessage" :title="errorMessage" type="error" :closable="false" show-icon class="auth-alert" />
          <el-button native-type="submit" type="primary" size="large" :loading="loading" class="auth-submit">{{ mode === 'staff' ? '使用邀请码注册' : '注册普通用户账号' }}</el-button>
        </el-form>
        <p class="auth-switch">已有账号？<router-link to="/login">直接登录</router-link></p>
        <router-link class="auth-back" to="/">← 返回公开门户</router-link>
      </div>
    </main>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { fetchCaptcha } from "../api/auth";
import AppearanceQuickControls from "../components/AppearanceQuickControls.vue";
import { useAuthStore } from "../stores/auth";
import { buildRegistrationPayload } from "../utils/registration";
import { dashboardRouteForRole } from "../utils/roles";

const router = useRouter();
const authStore = useAuthStore();
const mode = ref("user");
const form = reactive({ username: "", email: "", phone: "", password: "", invite_code: "", captcha_id: "", captcha_answer: "" });
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
const refreshCaptcha = () => { errorMessage.value = ""; return loadCaptcha(); };

async function onSubmit() {
  if (!form.username.trim() || form.password.length < 6 || !form.captcha_answer.trim()) {
    errorMessage.value = "请填写用户名、至少 6 位密码和验证码";
    return;
  }
  if (mode.value === "staff" && !form.invite_code.trim()) {
    errorMessage.value = "机构工作人员注册必须填写邀请码";
    return;
  }
  if (!form.captcha_id) {
    errorMessage.value = "验证码尚未加载完成";
    return;
  }
  loading.value = true;
  errorMessage.value = "";
  try {
    const payload = buildRegistrationPayload(mode.value, form);
    await authStore.registerUser(payload);
    const user = await authStore.fetchMe();
    await router.replace(dashboardRouteForRole(user.role));
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "注册失败，请检查填写信息";
    await loadCaptcha({ showError: false });
  } finally {
    loading.value = false;
  }
}

watch(mode, () => { errorMessage.value = ""; if (mode.value === "user") form.invite_code = ""; });
onMounted(loadCaptcha);
</script>
