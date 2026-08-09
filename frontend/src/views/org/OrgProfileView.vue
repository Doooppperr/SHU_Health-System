<template>
  <div class="workspace-page">
    <section class="page-intro">
      <div><p>公开服务信息</p><h2>机构资料维护</h2><span>机构身份信息已锁定，其他公开服务信息请保持准确、完整。</span></div>
      <el-button type="primary" :loading="saving" @click="save">保存修改</el-button>
    </section>

    <el-alert v-if="errorMessage" :title="errorMessage" type="error" show-icon :closable="false" />
    <el-card shadow="never" class="form-card" v-loading="loading">
      <el-alert
        title="机构身份信息已锁定；分院名称、所在区域或详细地址如需更正，请联系平台管理员。"
        type="success"
        show-icon
        :closable="false"
        style="margin-bottom: 14px"
      />
      <el-form :model="form" label-position="top" class="responsive-form-grid">
        <el-form-item label="所属机构主体"><el-input :model-value="organizationName" disabled /></el-form-item>
        <el-form-item label="分院 / 门店名称"><el-input v-model="form.branch_name" maxlength="120" disabled /></el-form-item>
        <el-form-item label="所在区域"><el-input v-model="form.district" maxlength="80" disabled /></el-form-item>
        <el-form-item label="咨询电话"><el-input v-model="form.consult_phone" maxlength="30" /></el-form-item>
        <el-form-item label="详细地址" class="form-grid-full"><el-input v-model="form.address" maxlength="255" disabled /></el-form-item>
        <el-form-item label="交通信息" class="form-grid-full"><el-input v-model="form.metro_info" maxlength="255" placeholder="地铁、公交及停车提示" /></el-form-item>
        <el-form-item label="分机号"><el-input v-model="form.ext" maxlength="20" /></el-form-item>
        <el-form-item label="轮休日"><el-input v-model="form.closed_day" maxlength="20" placeholder="例如：周日" /></el-form-item>
        <el-form-item label="分院简介" class="form-grid-full">
          <el-input v-model="form.description" type="textarea" :rows="6" maxlength="2000" show-word-limit placeholder="介绍本分院的交通、服务特色与体检流程" />
        </el-form-item>
      </el-form>
    </el-card>

    <AccountEmailPanel :email="authStore.user?.email || ''" />
    <AccountSecurityPanel :email="authStore.user?.email || ''" />

    <section id="institution-gallery"><OrgGalleryView /></section>

    <el-card shadow="never" class="org-danger-zone">
      <template #header><strong>注销机构账号</strong></template>
      <p>注销后当前分院账号将立即失效，分院与套餐会从公开页面撤下；历史预约、报告和投诉仍会保留。</p>
      <el-button type="danger" plain :loading="deactivationChecking" @click="openDeactivation">检查并申请注销</el-button>
    </el-card>

    <el-alert title="权限说明" description="机构主体和分院归属由系统管理员维护；当前账号只能编辑自己绑定分院的公开资料。" type="info" show-icon :closable="false" />

    <el-dialog v-model="deactivationVisible" title="注销机构账号" width="min(560px, 92vw)" :close-on-click-modal="false">
      <template v-if="deactivationCheck">
        <el-alert
          v-if="!deactivationCheck.can_deactivate"
          title="当前仍有未完成业务，暂时不能注销"
          type="warning"
          show-icon
          :closable="false"
        />
        <el-descriptions :column="1" border style="margin-top: 16px">
          <el-descriptions-item label="未来有效预约">{{ deactivationCheck.future_effective_appointments || 0 }}</el-descriptions-item>
          <el-descriptions-item label="已到检未完成报告">{{ deactivationCheck.arrived_unfinished_reports || 0 }}</el-descriptions-item>
          <el-descriptions-item label="草稿或待复核报告">{{ deactivationCheck.draft_or_pending_reports || 0 }}</el-descriptions-item>
          <el-descriptions-item label="未解决投诉">{{ deactivationCheck.unresolved_complaints || 0 }}</el-descriptions-item>
          <el-descriptions-item label="其他未完成上传任务">{{ deactivationCheck.other_upload_tasks || 0 }}</el-descriptions-item>
          <el-descriptions-item label="将随注销关闭的候补提醒">
            {{ deactivationCheck.active_waitlist_subscriptions || 0 }}
          </el-descriptions-item>
        </el-descriptions>
        <template v-if="deactivationCheck.can_deactivate">
          <el-alert title="注销后只能由平台管理员恢复；生效中的候补提醒将关闭并通知相关用户。" type="error" show-icon :closable="false" style="margin-top:16px" />
          <el-form-item label="当前账号密码" required style="margin-top:16px">
            <el-input v-model="deactivationPassword" type="password" show-password autocomplete="current-password" />
          </el-form-item>
        </template>
      </template>
      <template #footer>
        <el-button @click="deactivationVisible = false">取消</el-button>
        <el-button v-if="deactivationCheck?.can_deactivate" type="danger" :loading="deactivating" :disabled="!deactivationPassword" @click="confirmDeactivation">确认注销</el-button>
        <template v-else>
          <el-button v-if="deactivationCheck?.unresolved_complaints" type="warning" @click="goComplaints">查看未解决投诉</el-button>
          <el-button type="primary" @click="goReportTasks">查看未完成业务</el-button>
        </template>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { nextTick, onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import { useRoute, useRouter } from "vue-router";
import { deactivateOrgAccount, fetchOrgAccountDeactivationCheck, fetchOrgInstitution, updateOrgInstitution } from "../../api/org";
import AccountSecurityPanel from "../../components/AccountSecurityPanel.vue";
import AccountEmailPanel from "../../components/AccountEmailPanel.vue";
import { useAuthStore } from "../../stores/auth";
import OrgGalleryView from "./OrgGalleryView.vue";

const loading = ref(false);
const authStore = useAuthStore();
const route = useRoute();
const router = useRouter();
const saving = ref(false);
const errorMessage = ref("");
const organizationName = ref("");
const deactivationVisible = ref(false);
const deactivationChecking = ref(false);
const deactivating = ref(false);
const deactivationCheck = ref(null);
const deactivationPassword = ref("");
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
  saving.value = true;
  try {
    const editableFields = ["metro_info", "consult_phone", "ext", "closed_day", "description"];
    const payload = Object.fromEntries(
      editableFields.map((key) => [key, form[key].trim() || null]),
    );
    const { data } = await updateOrgInstitution(payload);
    assign(data.item || data.institution);
    ElMessage.success("机构资料已保存");
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "保存失败");
  } finally {
    saving.value = false;
  }
}

async function openDeactivation() {
  deactivationChecking.value = true;
  try {
    const { data } = await fetchOrgAccountDeactivationCheck();
    deactivationCheck.value = data.item || data;
    deactivationPassword.value = "";
    deactivationVisible.value = true;
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "暂时无法检查注销条件");
  } finally {
    deactivationChecking.value = false;
  }
}

function goReportTasks() {
  deactivationVisible.value = false;
  router.push({ name: "org-reports", query: { view: "archive" } });
}

function goComplaints() {
  deactivationVisible.value = false;
  router.push({ name: "org-complaints", query: { status: "institution_pending" } });
}

async function confirmDeactivation() {
  deactivating.value = true;
  try {
    await deactivateOrgAccount(deactivationPassword.value);
    authStore.logout();
    ElMessage.success("机构账号已注销");
    await router.replace({ name: "login" });
  } catch (error) {
    const data = error?.response?.data || {};
    if (data.code === "INSTITUTION_DEACTIVATION_BLOCKED") {
      deactivationCheck.value = { ...(data.blockers || {}), can_deactivate: false };
    }
    ElMessage.error(data.message || "账号注销失败");
  } finally {
    deactivating.value = false;
  }
}
onMounted(async()=>{await load();if(route.query.section==="gallery"){await nextTick();document.getElementById("institution-gallery")?.scrollIntoView({behavior:"smooth"});}});
</script>

<style scoped>
.org-danger-zone{border-color:#efc2c2}.org-danger-zone p{color:var(--el-text-color-secondary);line-height:1.7}
</style>
