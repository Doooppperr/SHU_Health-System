<template>
  <div class="workspace-page">
    <section class="page-intro">
      <div><p>公开服务信息</p><h2>机构资料维护</h2><span>这些信息会展示给普通用户，请保持准确、完整。</span></div>
      <el-button type="primary" :loading="saving" @click="save">保存修改</el-button>
    </section>

    <el-alert v-if="errorMessage" :title="errorMessage" type="error" show-icon :closable="false" />
    <el-card shadow="never" class="form-card" v-loading="loading">
      <el-form :model="form" label-position="top" class="responsive-form-grid">
        <el-form-item label="所属机构主体"><el-input :model-value="organizationName" disabled /></el-form-item>
        <el-form-item label="分院 / 门店名称" required><el-input v-model="form.branch_name" maxlength="120" /></el-form-item>
        <el-form-item label="所在区域" required><el-input v-model="form.district" maxlength="80" placeholder="例如：浦东新区" /></el-form-item>
        <el-form-item label="咨询电话"><el-input v-model="form.consult_phone" maxlength="30" /></el-form-item>
        <el-form-item label="详细地址" required class="form-grid-full"><el-input v-model="form.address" maxlength="255" /></el-form-item>
        <el-form-item label="交通信息" class="form-grid-full"><el-input v-model="form.metro_info" maxlength="255" placeholder="地铁、公交及停车提示" /></el-form-item>
        <el-form-item label="分机号"><el-input v-model="form.ext" maxlength="20" /></el-form-item>
        <el-form-item label="轮休日"><el-input v-model="form.closed_day" maxlength="20" placeholder="例如：周日" /></el-form-item>
        <el-form-item label="分院简介" class="form-grid-full">
          <el-input v-model="form.description" type="textarea" :rows="6" maxlength="2000" show-word-limit placeholder="介绍本分院的交通、服务特色与体检流程" />
        </el-form-item>
      </el-form>
    </el-card>

    <AccountSecurityPanel :email="authStore.user?.email || ''" />

    <section id="institution-gallery"><OrgGalleryView /></section>

    <el-alert title="权限说明" description="机构主体和分院归属由系统管理员维护；当前账号只能编辑自己绑定分院的公开资料。" type="info" show-icon :closable="false" />
  </div>
</template>

<script setup>
import { nextTick, onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import { useRoute } from "vue-router";
import { fetchOrgInstitution, updateOrgInstitution } from "../../api/org";
import AccountSecurityPanel from "../../components/AccountSecurityPanel.vue";
import { useAuthStore } from "../../stores/auth";
import OrgGalleryView from "./OrgGalleryView.vue";

const loading = ref(false);
const authStore = useAuthStore();
const route = useRoute();
const saving = ref(false);
const errorMessage = ref("");
const organizationName = ref("");
const form = reactive({ branch_name: "", district: "", address: "", metro_info: "", consult_phone: "", ext: "", closed_day: "", description: "" });

function assign(item = {}) {
  organizationName.value = item.organization?.name || item.name || "";
  Object.keys(form).forEach((key) => { form[key] = item[key] ?? ""; });
}
async function load() {
  loading.value = true;
  try {
    const { data } = await fetchOrgInstitution();
    assign(data.item || data.institution);
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "机构资料加载失败";
  } finally {
    loading.value = false;
  }
}
async function save() {
  if (!form.branch_name.trim() || !form.district.trim() || !form.address.trim()) {
    ElMessage.error("请完整填写分院、区域和地址");
    return;
  }
  saving.value = true;
  try {
    const { data } = await updateOrgInstitution(Object.fromEntries(Object.entries(form).map(([key, value]) => [key, value.trim() || null])));
    assign(data.item || data.institution);
    ElMessage.success("机构资料已保存");
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "保存失败");
  } finally {
    saving.value = false;
  }
}
onMounted(async()=>{await load();if(route.query.section==="gallery"){await nextTick();document.getElementById("institution-gallery")?.scrollIntoView({behavior:"smooth"});}});
</script>
