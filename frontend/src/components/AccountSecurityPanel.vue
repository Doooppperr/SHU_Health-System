<template>
  <el-card shadow="never" class="form-card">
    <template #header><div><strong>账户安全</strong><div style="margin-top:6px;color:var(--el-text-color-secondary)">修改密码需要同时验证当前密码和绑定邮箱。</div></div></template>
    <el-form :model="form" label-position="top" style="max-width:680px">
      <el-alert :title="`邮件验证码将发送到 ${maskedEmail}`" type="info" show-icon :closable="false" style="margin-bottom:18px" />
      <el-form-item label="当前密码"><el-input v-model="form.current_password" type="password" show-password autocomplete="current-password" /></el-form-item>
      <el-form-item label="邮件验证码">
        <div style="display:flex;gap:10px;width:100%"><el-input v-model="form.verification_code" maxlength="6" inputmode="numeric" placeholder="6 位数字验证码" /><el-button :loading="sending" :disabled="countdown>0" @click="sendCode">{{ countdown ? `${countdown} 秒后重发` : "发送验证码" }}</el-button></div>
      </el-form-item>
      <el-form-item label="新密码"><el-input v-model="form.new_password" type="password" show-password autocomplete="new-password" placeholder="至少 6 位" /></el-form-item>
      <el-form-item label="确认新密码"><el-input v-model="form.confirm_password" type="password" show-password autocomplete="new-password" /></el-form-item>
      <el-button type="primary" :loading="saving" @click="changePassword">确认修改密码</el-button>
    </el-form>
  </el-card>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import { useRouter } from "vue-router";
import { confirmPasswordChange, requestPasswordChangeCode } from "../api/auth";
import { useAuthStore } from "../stores/auth";

const props = defineProps({ email: { type: String, default: "" } });
const router = useRouter(), auth = useAuthStore();
const form = reactive({ current_password: "", verification_code: "", new_password: "", confirm_password: "" });
const challengeId = ref(""), sending = ref(false), saving = ref(false), countdown = ref(0);
let timer = null;
const maskedEmail = computed(() => { const [name, domain] = (props.email || "未绑定邮箱").split("@"); return domain ? `${name.slice(0,2)}***@${domain}` : name; });
function startCountdown() { countdown.value = 60; clearInterval(timer); timer = setInterval(() => { countdown.value -= 1; if (countdown.value <= 0) clearInterval(timer); }, 1000); }
async function sendCode() { sending.value = true; try { const { data } = await requestPasswordChangeCode(); challengeId.value = data.challenge_id; startCountdown(); ElMessage.success("验证码已发送到绑定邮箱"); } catch (error) { ElMessage.error(error?.response?.data?.message || "验证码发送失败"); } finally { sending.value = false; } }
async function changePassword() {
  if (!form.current_password) return ElMessage.warning("请输入当前密码");
  if (!challengeId.value || !/^\d{6}$/.test(form.verification_code)) return ElMessage.warning("请先获取并输入 6 位邮件验证码");
  if (form.new_password.length < 6) return ElMessage.warning("新密码至少需要 6 个字符");
  if (form.new_password !== form.confirm_password) return ElMessage.warning("两次输入的新密码不一致");
  saving.value = true;
  try { await confirmPasswordChange({ challenge_id: challengeId.value, current_password: form.current_password, verification_code: form.verification_code, new_password: form.new_password }); auth.logout(); ElMessage.success("密码修改成功，请重新登录"); await router.replace({ name: "login" }); }
  catch (error) { ElMessage.error(error?.response?.data?.message || "密码修改失败"); }
  finally { saving.value = false; }
}
onBeforeUnmount(() => clearInterval(timer));
</script>
