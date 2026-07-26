<template>
  <el-card shadow="never" class="form-card account-email-card">
    <template #header>
      <div>
        <strong>{{ isInstitution ? "账号与预约通知邮箱" : "绑定邮箱" }}</strong>
        <div class="account-email-description">
          {{ isInstitution ? "同一分院的所有管理员共用该邮箱，同时接收账号安全和预约业务通知。" : "预约、空位提醒和账户安全邮件都会发送到这里。" }}
        </div>
      </div>
    </template>
    <div class="account-email-row">
      <div><small>当前绑定邮箱</small><strong>{{ email || "尚未绑定" }}</strong></div>
      <el-button type="primary" plain @click="openDialog">修改绑定邮箱</el-button>
    </div>
  </el-card>

  <el-dialog v-model="visible" :title="isInstitution ? '修改账号与预约通知邮箱' : '修改绑定邮箱'" width="min(500px, 94vw)" destroy-on-close>
    <el-alert
      :title="isInstitution ? '修改后，本分院所有管理员的账号邮箱和预约通知邮箱会同时更新。' : '修改后，后续平台邮件会发送到新邮箱。'"
      type="warning"
      show-icon
      :closable="false"
    />
    <el-form label-position="top" style="margin-top:18px">
      <el-form-item label="当前邮箱"><el-input :model-value="email" disabled /></el-form-item>
      <el-form-item label="新邮箱"><el-input v-model.trim="newEmail" autocomplete="email" placeholder="请输入新的邮箱地址" @keyup.enter="submit" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible=false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="submit">确认修改</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { changeAccountEmail } from "../api/auth";
import { useAuthStore } from "../stores/auth";

const props = defineProps({ email: { type: String, default: "" } });
const emit = defineEmits(["changed"]);
const authStore = useAuthStore();
const visible = ref(false), saving = ref(false), newEmail = ref("");
const isInstitution = computed(() => authStore.user?.role === "institution_admin");
const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function openDialog() {
  newEmail.value = "";
  visible.value = true;
}

async function submit() {
  const normalized = newEmail.value.trim().toLowerCase();
  if (!emailPattern.test(normalized)) return ElMessage.warning("请输入有效的新邮箱地址");
  if (normalized === String(props.email || "").toLowerCase()) return ElMessage.warning("新邮箱不能与当前绑定邮箱相同");
  try {
    await ElMessageBox.confirm(
      `确认将绑定邮箱从 ${props.email || "当前邮箱"} 修改为 ${normalized}？修改成功后，旧邮箱和新邮箱都会收到通知。`,
      "确认修改绑定邮箱",
      { type: "warning", confirmButtonText: "确认修改", cancelButtonText: "再检查一下" },
    );
  } catch {
    return;
  }
  saving.value = true;
  try {
    const { data } = await changeAccountEmail(normalized);
    authStore.user = data.user;
    authStore.persist();
    visible.value = false;
    emit("changed", data.user);
    ElMessage.success(data.message || "绑定邮箱已修改");
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "邮箱修改失败");
  } finally {
    saving.value = false;
  }
}
</script>

<style scoped>
.account-email-description{margin-top:6px;color:var(--el-text-color-secondary);line-height:1.6}.account-email-row{display:flex;align-items:center;justify-content:space-between;gap:18px;max-width:760px}.account-email-row>div{display:grid;gap:6px}.account-email-row small{color:var(--el-text-color-secondary)}.account-email-row strong{overflow-wrap:anywhere;font-size:17px}@media(max-width:600px){.account-email-row{align-items:stretch;flex-direction:column}.account-email-row :deep(.el-button){width:100%}}
</style>
