<template>
  <div class="page-shell">
    <el-card class="auth-card">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span>注册</span>
          <el-button link type="primary" @click="goLogin">去登录</el-button>
        </div>
      </template>

      <el-form :model="form" label-position="top" @submit.prevent="onSubmit">
        <el-form-item label="用户名">
          <el-input v-model="form.username" placeholder="请输入用户名" />
        </el-form-item>

        <el-form-item label="邮箱">
          <el-input v-model="form.email" placeholder="请输入邮箱（可选）" />
        </el-form-item>

        <el-form-item label="手机号">
          <el-input v-model="form.phone" placeholder="请输入手机号（可选）" />
        </el-form-item>

        <el-form-item label="密码">
          <el-input v-model="form.password" show-password placeholder="至少6位密码" />
        </el-form-item>

        <el-form-item label="验证码">
          <div class="captcha-row">
            <el-input
              v-model="form.captcha_answer"
              maxlength="4"
              placeholder="请输入图片验证码"
              autocomplete="off"
              @keyup.enter="onSubmit"
            />
            <button
              class="captcha-image-button"
              type="button"
              :disabled="captchaLoading"
              title="刷新验证码"
              @click="refreshCaptcha"
            >
              <img v-if="captchaImage" :src="captchaImage" alt="验证码" />
              <span v-else>加载中</span>
            </button>
          </div>
        </el-form-item>

        <el-alert v-if="errorMessage" :title="errorMessage" type="error" :closable="false" style="margin-bottom: 12px" />

        <el-button type="primary" :loading="loading" style="width: 100%" @click="onSubmit">
          注册
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";

import { fetchCaptcha } from "../api/auth";
import { useAuthStore } from "../stores/auth";

const router = useRouter();
const authStore = useAuthStore();

const form = reactive({
  username: "",
  email: "",
  phone: "",
  password: "",
  captcha_id: "",
  captcha_answer: "",
});

const loading = ref(false);
const captchaLoading = ref(false);
const captchaImage = ref("");
const errorMessage = ref("");

const loadCaptcha = async ({ showError = true } = {}) => {
  captchaLoading.value = true;

  try {
    const { data } = await fetchCaptcha();
    form.captcha_id = data.captcha_id;
    form.captcha_answer = "";
    captchaImage.value = data.image;
  } catch {
    form.captcha_id = "";
    captchaImage.value = "";
    if (showError) {
      errorMessage.value = "验证码加载失败，请刷新页面重试";
    }
  } finally {
    captchaLoading.value = false;
  }
};

const refreshCaptcha = async () => {
  errorMessage.value = "";
  await loadCaptcha();
};

const onSubmit = async () => {
  if (!form.username || !form.password || !form.captcha_answer) {
    errorMessage.value = "用户名、密码和验证码为必填项";
    return;
  }

  if (!form.captcha_id) {
    errorMessage.value = "验证码未加载完成，请刷新验证码";
    return;
  }

  loading.value = true;
  errorMessage.value = "";

  try {
    await authStore.registerUser(form);
    await authStore.fetchMe();
    router.push({ name: "institutions" });
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "注册失败，请重试";
    await loadCaptcha({ showError: false });
  } finally {
    loading.value = false;
  }
};

const goLogin = () => {
  router.push({ name: "login" });
};

onMounted(() => {
  loadCaptcha();
});
</script>
