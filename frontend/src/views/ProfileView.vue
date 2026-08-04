<template>
  <div class="workspace-page">
    <section class="page-intro">
      <div>
        <p>健康身份</p>
        <h2>个人资料</h2>
        <span>健康身份码由平台生成且不可修改，请在体检时与真实姓名一并提供给机构。</span>
      </div>
    </section>
    <el-card shadow="never" v-loading="loading">
      <el-alert
        v-if="identityLocked"
        title="实名认证信息已锁定，如姓名、性别或出生日期有误，请联系平台管理员更正。"
        type="success"
        show-icon
        :closable="false"
        style="margin-bottom: 14px"
      />
      <el-alert
        v-else
        title="请准确填写实名信息。首次提交后，姓名、性别与出生日期将不可自行修改。"
        type="warning"
        show-icon
        :closable="false"
        style="margin-bottom: 14px"
      />
      <el-alert title="健康身份码不会向亲友、评论接口或 AI 模型公开" type="info" show-icon :closable="false" />
      <div class="invite-code-box">
        <small>我的健康身份码</small>
        <strong>{{ form.health_id || '-' }}</strong>
        <el-button @click="copyHealthId">复制</el-button>
      </div>
      <div class="health-id-booking-permission">
        <div>
          <strong>允许使用健康身份码为我代预约</strong>
          <small>
            开启后，你当面提供身份码时，对方可获得一次性预约凭证；对方看不到你的健康档案。
            关闭会让未使用凭证和相关空位提醒立即失效，但不会取消已经成立的正式预约。
          </small>
        </div>
        <el-switch
          v-model="form.allow_health_id_proxy_booking"
          active-text="允许"
          inactive-text="关闭"
        />
      </div>
      <el-form label-position="top" style="max-width: 760px">
        <div class="responsive-form-grid">
          <el-form-item label="真实姓名"><el-input v-model="form.real_name" :disabled="identityLocked" /></el-form-item>
          <el-form-item label="出生日期">
            <el-date-picker v-model="form.birth_date" type="date" value-format="YYYY-MM-DD" :disabled="identityLocked" style="width: 100%" />
          </el-form-item>
          <el-form-item label="性别">
            <el-select v-model="form.gender" clearable :disabled="identityLocked" style="width: 100%">
              <el-option label="男" value="male" />
              <el-option label="女" value="female" />
              <el-option label="其他" value="other" />
              <el-option label="不披露" value="undisclosed" />
            </el-select>
          </el-form-item>
          <el-form-item label="手机号"><el-input v-model="form.phone" /></el-form-item>
          <el-form-item label="通知邮箱">
            <el-input :model-value="form.email" disabled />
            <div class="email-status">
              <el-tag v-if="form.email" type="success" effect="plain">已绑定</el-tag>
              <span>{{ form.email ? "请在下方修改绑定邮箱" : "邮箱为注册和通知必填项" }}</span>
            </div>
          </el-form-item>
        </div>
        <el-form-item label="过敏史"><el-input v-model="form.allergy_history" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="既往史"><el-input v-model="form.medical_history" type="textarea" :rows="3" /></el-form-item>
        <el-button type="primary" :loading="saving" @click="save">
          {{ identityLocked ? "保存可修改资料" : "提交实名认证" }}
        </el-button>
      </el-form>
    </el-card>
    <AccountEmailPanel :email="form.email" @changed="emailChanged" />
    <AccountSecurityPanel :email="form.email" />
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { completeBasicProfile, fetchProfile, updateProfile } from "../api/profile";
import AccountSecurityPanel from "../components/AccountSecurityPanel.vue";
import AccountEmailPanel from "../components/AccountEmailPanel.vue";
import { useAuthStore } from "../stores/auth";
import { isBasicProfileComplete } from "../utils/v12";

const authStore = useAuthStore();
const loading = ref(false);
const saving = ref(false);
const initialHealthIdBookingEnabled = ref(true);
const form = reactive({
  health_id: "",
  real_name: "",
  birth_date: null,
  gender: null,
  email: "",
  email_verified_at: null,
  phone: "",
  allergy_history: "",
  medical_history: "",
  allow_health_id_proxy_booking: true,
  profile_completed: false,
});
const identityLocked = computed(() => isBasicProfileComplete(form));

async function load() {
  loading.value = true;
  try {
    const { data } = await fetchProfile();
    Object.assign(form, data.item);
    form.allow_health_id_proxy_booking = (
      data.item.allow_health_id_proxy_booking
      ?? data.item.health_id_booking_enabled
      ?? true
    );
    initialHealthIdBookingEnabled.value = form.allow_health_id_proxy_booking !== false;
  } finally {
    loading.value = false;
  }
}

async function save() {
  if (!identityLocked.value && (!form.real_name || !form.birth_date || !form.gender)) {
    ElMessage.warning("请完整填写真实姓名、性别和出生日期");
    return;
  }
  if (initialHealthIdBookingEnabled.value && form.allow_health_id_proxy_booking === false) {
    try {
      await ElMessageBox.confirm(
        "关闭后，尚未使用的一次性代预约凭证和相关空位提醒会立即失效；已经成立的正式预约不会取消。",
        "关闭健康身份码代预约",
        { type: "warning", confirmButtonText: "确认关闭", cancelButtonText: "继续开启" },
      );
    } catch {
      form.allow_health_id_proxy_booking = true;
      return;
    }
  }
  saving.value = true;
  try {
    if (!identityLocked.value) {
      await completeBasicProfile({
        real_name: form.real_name,
        birth_date: form.birth_date,
        gender: form.gender,
      });
    }
    await updateProfile({
      phone: form.phone,
      allergy_history: form.allergy_history,
      medical_history: form.medical_history,
      allow_health_id_proxy_booking: form.allow_health_id_proxy_booking,
    });
    ElMessage.success(identityLocked.value ? "个人资料已保存" : "实名认证已完成");
    await load();
    authStore.user = { ...authStore.user, ...form };
    authStore.persist();
    window.dispatchEvent(new CustomEvent("healthdoc-profile-completed", { detail: { ...form } }));
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "保存失败");
  } finally {
    saving.value = false;
  }
}

function emailChanged(user) {
  form.email = user?.email || "";
  form.email_verified_at = user?.email_verified_at || null;
}

async function copyHealthId() {
  try {
    await navigator.clipboard.writeText(form.health_id);
    ElMessage.success("健康身份码已复制");
  } catch {
    ElMessage.warning("请手动复制健康身份码");
  }
}

onMounted(load);
</script>

<style scoped>
.email-status {
  display: flex;
  align-items: center;
  min-height: 28px;
  margin-top: 6px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.health-id-booking-permission {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin: 14px 0 20px;
  padding: 16px;
  border: 1px solid var(--el-border-color);
  border-radius: 14px;
  background: var(--el-fill-color-extra-light);
}

.health-id-booking-permission > div {
  display: grid;
  gap: 6px;
}

.health-id-booking-permission small {
  color: var(--el-text-color-secondary);
  line-height: 1.55;
}

@media (max-width: 640px) {
  .health-id-booking-permission {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
