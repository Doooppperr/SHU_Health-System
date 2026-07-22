<template>
  <div class="auth-page">
    <header class="auth-header">
      <router-link class="auth-brand" to="/"><span>H</span><strong>康康健健 HealthDoc</strong></router-link>
      <AppearanceQuickControls />
    </header>
    <main id="main-content" class="auth-main" tabindex="-1">
      <div class="auth-panel">
        <div class="auth-heading"><p>账户安全</p><h1>找回密码</h1><span>验证用户名、绑定邮箱和验证码后设置新密码。</span></div>
        <el-form :model="form" label-position="top" @submit.prevent="submit">
          <template v-if="!challengeId">
            <el-form-item label="用户名"><el-input v-model="form.username" size="large" autocomplete="username" placeholder="请输入用户名" /></el-form-item>
            <el-form-item label="绑定邮箱"><el-input v-model="form.email" size="large" autocomplete="email" placeholder="请输入注册时绑定的邮箱" /></el-form-item>
            <el-form-item label="图片验证码">
              <div class="captcha-row">
                <el-input v-model="form.captcha_answer" size="large" maxlength="4" placeholder="输入验证码" />
                <button class="captcha-image-button" type="button" :disabled="captchaLoading" aria-label="刷新图片验证码" @click="loadCaptcha">
                  <img v-if="captchaImage" :src="captchaImage" alt="图片验证码" /><span v-else>加载中</span>
                </button>
              </div>
            </el-form-item>
            <el-button class="auth-submit" type="primary" native-type="submit" size="large" :loading="loading">发送邮件验证码</el-button>
          </template>
          <template v-else>
            <el-alert title="验证码已发送。若账号信息匹配，请在 10 分钟内完成验证。" type="success" show-icon :closable="false" class="auth-alert" />
            <el-form-item label="邮件验证码"><el-input v-model="form.verification_code" size="large" maxlength="6" inputmode="numeric" placeholder="请输入 6 位数字验证码" /></el-form-item>
            <el-form-item label="新密码"><el-input v-model="form.new_password" size="large" type="password" show-password autocomplete="new-password" placeholder="至少 6 位" /></el-form-item>
            <el-form-item label="确认新密码"><el-input v-model="form.confirm_password" size="large" type="password" show-password autocomplete="new-password" placeholder="再次输入新密码" /></el-form-item>
            <el-button class="auth-submit" type="primary" native-type="submit" size="large" :loading="loading">重置密码</el-button>
            <el-button style="width:100%;margin:12px 0 0" @click="restart">重新获取验证码</el-button>
          </template>
          <el-alert v-if="errorMessage" :title="errorMessage" type="error" show-icon :closable="false" class="auth-alert" />
        </el-form>
        <p class="auth-switch"><router-link to="/login">返回登录</router-link></p>
      </div>
    </main>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import { useRouter } from "vue-router";
import AppearanceQuickControls from "../components/AppearanceQuickControls.vue";
import { confirmPasswordReset, fetchCaptcha, requestPasswordResetCode } from "../api/auth";

const router = useRouter();
const loading = ref(false), captchaLoading = ref(false), captchaImage = ref(""), challengeId = ref(""), errorMessage = ref("");
const form = reactive({ username: "", email: "", captcha_id: "", captcha_answer: "", verification_code: "", new_password: "", confirm_password: "" });

async function loadCaptcha() {
  captchaLoading.value = true;
  try { const { data } = await fetchCaptcha(); form.captcha_id = data.captcha_id; form.captcha_answer = ""; captchaImage.value = data.image; }
  catch { errorMessage.value = "图片验证码加载失败，请稍后重试"; }
  finally { captchaLoading.value = false; }
}
function restart() { challengeId.value = ""; form.verification_code = ""; form.new_password = ""; form.confirm_password = ""; errorMessage.value = ""; loadCaptcha(); }
async function submit() {
  errorMessage.value = "";
  if (!challengeId.value) {
    if (!form.username.trim() || !form.email.trim() || !form.captcha_answer.trim()) { errorMessage.value = "请完整填写用户名、绑定邮箱和图片验证码"; return; }
    loading.value = true;
    try { const { data } = await requestPasswordResetCode({ username: form.username.trim(), email: form.email.trim(), captcha_id: form.captcha_id, captcha_answer: form.captcha_answer.trim() }); challengeId.value = data.challenge_id; ElMessage.success(data.message); }
    catch (error) { errorMessage.value = error?.response?.data?.message || "验证码发送失败，请稍后重试"; await loadCaptcha(); }
    finally { loading.value = false; }
    return;
  }
  if (!/^\d{6}$/.test(form.verification_code)) { errorMessage.value = "请输入 6 位数字邮件验证码"; return; }
  if (form.new_password.length < 6) { errorMessage.value = "新密码至少需要 6 个字符"; return; }
  if (form.new_password !== form.confirm_password) { errorMessage.value = "两次输入的新密码不一致"; return; }
  loading.value = true;
  try { await confirmPasswordReset({ challenge_id: challengeId.value, verification_code: form.verification_code, new_password: form.new_password }); ElMessage.success("密码已重置，请使用新密码登录"); await router.replace({ name: "login" }); }
  catch (error) { errorMessage.value = error?.response?.data?.message || "密码重置失败，请重新验证"; }
  finally { loading.value = false; }
}
onMounted(loadCaptcha);
</script>
