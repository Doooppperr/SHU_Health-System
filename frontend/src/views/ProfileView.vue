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
      <el-alert title="健康身份码不会向亲友、评论接口或 AI 模型公开" type="info" show-icon :closable="false" />
      <div class="invite-code-box">
        <small>我的健康身份码</small>
        <strong>{{ form.health_id || '-' }}</strong>
        <el-button @click="copyHealthId">复制</el-button>
      </div>
      <el-form label-position="top" style="max-width: 760px">
        <div class="responsive-form-grid">
          <el-form-item label="真实姓名"><el-input v-model="form.real_name" /></el-form-item>
          <el-form-item label="出生日期">
            <el-date-picker v-model="form.birth_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
          </el-form-item>
          <el-form-item label="性别">
            <el-select v-model="form.gender" clearable style="width: 100%">
              <el-option label="男" value="male" />
              <el-option label="女" value="female" />
              <el-option label="其他" value="other" />
              <el-option label="不披露" value="undisclosed" />
            </el-select>
          </el-form-item>
          <el-form-item label="手机号"><el-input v-model="form.phone" /></el-form-item>
          <el-form-item label="通知邮箱">
            <el-input v-model="form.email" placeholder="空位提醒需要绑定并验证邮箱" />
            <div class="email-status">
              <el-tag v-if="form.email" type="success" effect="plain">已绑定</el-tag>
              <span v-else>邮箱为注册和通知必填项</span>
            </div>
          </el-form-item>
        </div>
        <el-form-item label="过敏史"><el-input v-model="form.allergy_history" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="既往史"><el-input v-model="form.medical_history" type="textarea" :rows="3" /></el-form-item>
        <el-button type="primary" :loading="saving" @click="save">保存资料</el-button>
      </el-form>
    </el-card>
    <AccountSecurityPanel :email="form.email" />
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import { fetchProfile, updateProfile } from "../api/profile";
import AccountSecurityPanel from "../components/AccountSecurityPanel.vue";

const loading = ref(false);
const saving = ref(false);
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
});

async function load() {
  loading.value = true;
  try {
    const { data } = await fetchProfile();
    Object.assign(form, data.item);
  } finally {
    loading.value = false;
  }
}

async function save() {
  saving.value = true;
  try {
    const payload = { ...form };
    delete payload.health_id;
    delete payload.email_verified_at;
    await updateProfile(payload);
    ElMessage.success("个人资料已保存");
    await load();
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "保存失败");
  } finally {
    saving.value = false;
  }
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
</style>
