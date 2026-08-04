<template>
  <div v-if="incomplete && !loading" class="profile-gate-banner" role="status">
    <span>
      <strong>请先完成实名认证</strong>
      未实名前，日常测量、体检预约等健康服务暂不可用。
    </span>
    <el-button type="primary" size="small" @click="visible = true">立即认证</el-button>
  </div>

  <el-dialog
    v-model="visible"
    title="完成实名认证"
    width="min(520px, 92vw)"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
  >
    <el-alert
      title="姓名、性别和出生日期提交后将锁定；如需更正，请联系平台管理员。"
      type="warning"
      show-icon
      :closable="false"
    />
    <el-form ref="formRef" :model="form" :rules="rules" label-position="top" class="profile-gate-form">
      <el-form-item label="真实姓名" prop="real_name">
        <el-input v-model.trim="form.real_name" maxlength="40" placeholder="请填写与证件一致的姓名" />
      </el-form-item>
      <div class="profile-gate-grid">
        <el-form-item label="性别" prop="gender">
          <el-select v-model="form.gender" placeholder="请选择" style="width: 100%">
            <el-option label="男" value="male" />
            <el-option label="女" value="female" />
            <el-option label="其他" value="other" />
            <el-option label="不披露" value="undisclosed" />
          </el-select>
        </el-form-item>
        <el-form-item label="出生日期" prop="birth_date">
          <el-date-picker
            v-model="form.birth_date"
            type="date"
            value-format="YYYY-MM-DD"
            :disabled-date="disableFutureDate"
            style="width: 100%"
          />
        </el-form-item>
      </div>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">稍后完善</el-button>
      <el-button type="primary" :loading="saving" @click="submit">确认并锁定</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";

import { completeBasicProfile, fetchProfile } from "../api/profile";
import { useAuthStore } from "../stores/auth";
import { isBasicProfileComplete } from "../utils/v12";

const authStore = useAuthStore();
const loading = ref(true);
const saving = ref(false);
const visible = ref(false);
const formRef = ref(null);
const profile = ref({});
const form = reactive({
  real_name: "",
  gender: "",
  birth_date: "",
});
const rules = {
  real_name: [{ required: true, message: "请填写真实姓名", trigger: "blur" }],
  gender: [{ required: true, message: "请选择性别", trigger: "change" }],
  birth_date: [{ required: true, message: "请选择出生日期", trigger: "change" }],
};

const incomplete = computed(() => !isBasicProfileComplete(profile.value));

function disableFutureDate(date) {
  const today = new Date();
  today.setHours(23, 59, 59, 999);
  return date > today;
}

function open() {
  if (incomplete.value) visible.value = true;
}

async function load() {
  loading.value = true;
  try {
    const { data } = await fetchProfile();
    profile.value = data.item || data.user || data || {};
    authStore.user = { ...authStore.user, ...profile.value };
    authStore.persist();
    Object.assign(form, {
      real_name: profile.value.real_name || "",
      gender: profile.value.gender || "",
      birth_date: profile.value.birth_date || "",
    });
    if (incomplete.value) visible.value = true;
  } catch {
    profile.value = authStore.user || {};
    if (incomplete.value) visible.value = true;
  } finally {
    loading.value = false;
  }
}

async function submit() {
  try {
    await formRef.value?.validate();
    saving.value = true;
    const { data } = await completeBasicProfile({
      real_name: form.real_name,
      gender: form.gender,
      birth_date: form.birth_date,
    });
    const completed = data.item || data.user || {
      ...form,
      identity_completed: true,
      profile_completed: true,
      basic_profile_completed: true,
    };
    profile.value = {
      ...profile.value,
      ...completed,
      identity_completed: true,
      profile_completed: true,
    };
    authStore.user = {
      ...authStore.user,
      ...completed,
      identity_completed: true,
      profile_completed: true,
    };
    authStore.persist();
    visible.value = false;
    window.dispatchEvent(new CustomEvent("healthdoc-profile-completed", { detail: profile.value }));
    ElMessage.success("实名认证已完成，身份信息已锁定");
  } catch (error) {
    if (error?.fields) return;
    ElMessage.error(error?.response?.data?.message || error?.message || "实名认证提交失败");
  } finally {
    saving.value = false;
  }
}

onMounted(() => {
  window.addEventListener("healthdoc-open-profile-gate", open);
  load();
});
onBeforeUnmount(() => window.removeEventListener("healthdoc-open-profile-gate", open));
</script>

<style scoped>
.profile-gate-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin: 0 0 18px;
  padding: 12px 16px;
  border: 1px solid #f3c985;
  border-radius: 14px;
  background: #fff8e8;
  color: #8a5514;
}

.profile-gate-banner span {
  display: grid;
  gap: 2px;
}

.profile-gate-form {
  margin-top: 18px;
}

.profile-gate-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}

@media (max-width: 620px) {
  .profile-gate-banner {
    align-items: flex-start;
    flex-direction: column;
  }

  .profile-gate-grid {
    grid-template-columns: 1fr;
    gap: 0;
  }
}
</style>
