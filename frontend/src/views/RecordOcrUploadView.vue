<template>
  <div class="home-shell">
    <el-card class="home-card">
      <template #header>
        <div class="top-bar">
          <span>OCR上传解析</span>
          <MainNavActions>
            <template #prefix>
              <el-button plain @click="goRecords">返回档案列表</el-button>
            </template>
          </MainNavActions>
        </div>
      </template>

      <el-alert
        v-if="errorMessage"
        :title="errorMessage"
        type="error"
        :closable="false"
        style="margin-bottom: 16px"
      />

      <el-form label-width="100px" :model="form" class="ocr-form">
        <el-form-item label="档案归属人" required>
          <el-select v-model="form.owner_id" placeholder="请选择档案归属人" style="width: 100%">
            <el-option
              v-for="owner in ownerOptions"
              :key="owner.id"
              :label="owner.label"
              :value="owner.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="体检日期" required>
          <el-date-picker
            v-model="form.exam_date"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="请选择体检日期"
            style="width: 100%"
          />
        </el-form-item>

        <el-form-item label="体检机构（可选）">
          <el-select v-model="form.institution_id" clearable placeholder="暂不选取" style="width: 100%" @change="onInstitutionChange">
            <el-option
              v-for="institution in institutions"
              :key="institution.id"
              :label="`${institution.name} · ${institution.branch_name}`"
              :value="institution.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="体检套餐（可选）">
          <el-select v-model="form.package_id" clearable placeholder="暂不选取" style="width: 100%" @change="onPackageChange">
            <el-option v-for="pkg in currentPackages" :key="pkg.id" :label="packageLabel(pkg)" :value="pkg.id" />
          </el-select>
        </el-form-item>

        <el-form-item v-if="form.institution_id">
          <el-alert
            title="确认入档后，标准化档案信息和指标会自动向对应机构管理员只读开放。联系方式和原始报告不会开放。"
            type="warning"
            show-icon
            :closable="false"
          />
        </el-form-item>

        <el-form-item v-else>
          <el-alert
            title="未关联机构的档案只用于你的个人健康管理，不会向任何机构管理员展示。"
            type="info"
            show-icon
            :closable="false"
          />
        </el-form-item>

        <el-form-item label="报告文件" required>
          <el-upload
            :auto-upload="false"
            :limit="1"
            :on-change="onFileChange"
            :on-remove="onFileRemove"
            :file-list="fileList"
            accept=".pdf,.png,.jpg,.jpeg,.webp"
          >
            <el-button type="primary" plain>选择文件</el-button>
            <template #tip>
              <div class="el-upload__tip">支持 PDF/JPG/PNG，大小不超过 20MB</div>
            </template>
          </el-upload>
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :loading="uploadLoading" @click="submitUpload">上传并解析</el-button>
        </el-form-item>
      </el-form>

      <el-card v-if="uploadResult" shadow="never" style="margin-top: 18px">
        <template #header>
          <div class="top-bar">
            <span>OCR解析结果</span>
            <div class="top-actions wrap-actions">
              <el-tag :type="uploadResult.item.status === 'confirmed' ? 'success' : 'warning'">
                {{ uploadResult.item.status }}
              </el-tag>
              <el-button
                v-if="uploadResult.item.status !== 'confirmed'"
                type="success"
                :loading="confirmLoading"
                @click="confirmParsedRecord"
              >
                确认入档
              </el-button>
              <el-button type="primary" plain @click="goRecordDetail(uploadResult.item.id)">去修正指标</el-button>
            </div>
          </div>
        </template>

        <el-descriptions :column="1" border style="margin-bottom: 12px">
          <el-descriptions-item label="档案ID">{{ uploadResult.item.id }}</el-descriptions-item>
          <el-descriptions-item label="档案归属人">{{ uploadResult.item.owner?.username || "-" }}</el-descriptions-item>
          <el-descriptions-item label="上传人">{{ uploadResult.item.uploader?.username || "-" }}</el-descriptions-item>
          <el-descriptions-item label="OCR引擎">{{ uploadResult.ocr.provider }}</el-descriptions-item>
          <el-descriptions-item label="映射成功">{{ uploadResult.ocr.mapped_count }}</el-descriptions-item>
          <el-descriptions-item label="未匹配字段">{{ uploadResult.ocr.unmatched_count }}</el-descriptions-item>
          <el-descriptions-item label="过滤字段">{{ uploadResult.ocr.filtered_count || 0 }}</el-descriptions-item>
        </el-descriptions>

        <el-card
          shadow="never"
          v-if="uploadResult.item.status !== 'confirmed' && mappingDraftRows.length"
          style="margin-bottom: 12px"
        >
          <template #header>
            <span>候选映射确认（确认入档前可调整）</span>
          </template>

          <el-table :data="mappingDraftRows" border>
            <el-table-column prop="label" label="OCR字段名" min-width="180" />
            <el-table-column prop="value" label="OCR值" min-width="130" />
            <el-table-column label="建议指标" min-width="190">
              <template #default="scope">
                {{ scope.row.suggested_code }} - {{ scope.row.suggested_name }}
              </template>
            </el-table-column>
            <el-table-column label="确认入档指标" min-width="240">
              <template #default="scope">
                <el-select
                  v-model="scope.row.indicator_dict_id"
                  filterable
                  clearable
                  placeholder="可改选指标"
                  style="width: 100%"
                >
                  <el-option
                    v-for="dict in indicatorDicts"
                    :key="dict.id"
                    :label="`${dict.code} - ${dict.name}`"
                    :value="dict.id"
                  />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="置信度" width="100">
              <template #default="scope">
                {{ Number(scope.row.score || 0).toFixed(2) }}
              </template>
            </el-table-column>
            <el-table-column label="忽略" width="90">
              <template #default="scope">
                <el-switch v-model="scope.row.ignored" />
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-table :data="uploadResult.item.indicators || []" border style="margin-bottom: 12px">
          <el-table-column label="指标" min-width="220">
            <template #default="scope">
              {{ scope.row.indicator?.code }} - {{ scope.row.indicator?.name }}
            </template>
          </el-table-column>
          <el-table-column prop="value" label="值" min-width="120" />
          <el-table-column label="异常" width="100">
            <template #default="scope">
              <el-tag :type="scope.row.is_abnormal ? 'danger' : 'success'">
                {{ scope.row.is_abnormal ? '异常' : '正常' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="source" label="来源" width="90" />
        </el-table>

        <el-card shadow="never" v-if="uploadResult.ocr.unmatched_fields?.length">
          <template #header>
            <span>未匹配字段（可在档案详情页手动修正）</span>
          </template>
          <el-table :data="uploadResult.ocr.unmatched_fields" border>
            <el-table-column prop="label" label="OCR字段名" min-width="180" />
            <el-table-column prop="value" label="OCR值" min-width="140" />
          </el-table>
        </el-card>
      </el-card>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import { useRouter } from "vue-router";

import MainNavActions from "../components/MainNavActions.vue";
import { fetchFriends } from "../api/friends";
import { fetchIndicatorDicts } from "../api/indicators";
import { fetchInstitutions, fetchInstitutionPackages } from "../api/institutions";
import { confirmRecord, uploadRecordByOcr } from "../api/records";
import { fetchUsers } from "../api/users";
import { useAuthStore } from "../stores/auth";
import {
  buildOcrConfirmedMappings,
  createOcrMappingRows,
} from "../utils/ocrConfirmation";

const router = useRouter();
const authStore = useAuthStore();

const institutions = ref([]);
const packageMap = ref({});
const manageableFriends = ref([]);
const adminUsers = ref([]);
const indicatorDicts = ref([]);
const mappingDraftRows = ref([]);
const selectedFile = ref(null);
const fileList = ref([]);

const uploadLoading = ref(false);
const confirmLoading = ref(false);
const errorMessage = ref("");
const uploadResult = ref(null);

const form = reactive({
  owner_id: null,
  exam_date: "",
  institution_id: null,
  package_id: null,
});

const currentPackages = computed(() => {
  if (form.institution_id) return packageMap.value[form.institution_id] || [];
  return Object.values(packageMap.value).flat();
});

const packageLabel = (pkg) => {
  if (form.institution_id) return pkg.name;
  const institution = institutions.value.find((item) => item.id === pkg.institution_id);
  return institution ? `${pkg.name} · ${institution.name}` : pkg.name;
};

const ownerOptions = computed(() => {
  if (authStore.user?.role === "admin") {
    return adminUsers.value.map((user) => ({
      id: user.id,
      label: user.role === "admin" ? `${user.username}（管理员）` : `${user.username}（用户）`,
    }));
  }

  const selfUser = authStore.user
    ? [{ id: authStore.user.id, label: `${authStore.user.username}（本人）` }]
    : [];
  const friendUsers = manageableFriends.value
    .filter((relation) => relation.friend_user?.id)
    .map((relation) => ({
      id: relation.friend_user.id,
      label: `${relation.friend_user.username}（亲友）`,
    }));
  return [...selfUser, ...friendUsers];
});

const loadInstitutions = async () => {
  const { data } = await fetchInstitutions();
  institutions.value = data.items || [];
};

const loadFriends = async () => {
  const { data } = await fetchFriends();
  manageableFriends.value = data.manageable || [];
};

const loadAdminUsers = async () => {
  const { data } = await fetchUsers();
  adminUsers.value = data.items || [];
};

const loadIndicatorDicts = async () => {
  const { data } = await fetchIndicatorDicts();
  indicatorDicts.value = data.items || [];
};

const loadPackages = async (institutionId) => {
  if (!institutionId) {
    return;
  }

  if (packageMap.value[institutionId]) {
    return;
  }

  const { data } = await fetchInstitutionPackages(institutionId);
  packageMap.value = {
    ...packageMap.value,
    [institutionId]: data.items || [],
  };
};

const onInstitutionChange = async (institutionId) => {
  form.package_id = null;
  if (!institutionId) return;
  try {
    await loadPackages(institutionId);
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "套餐加载失败";
  }
};

const onPackageChange = (packageId) => {
  if (!packageId) return;
  const pkg = Object.values(packageMap.value).flat().find((item) => item.id === packageId);
  if (pkg?.institution_id && form.institution_id !== pkg.institution_id) {
    form.institution_id = pkg.institution_id;
  }
};

const onFileChange = (uploadFile, uploadFiles) => {
  selectedFile.value = uploadFile.raw;
  fileList.value = uploadFiles.slice(-1);
};

const onFileRemove = () => {
  selectedFile.value = null;
  fileList.value = [];
};

const submitUpload = async () => {
  if (!form.owner_id || !form.exam_date) {
    ElMessage.error("请先填写档案归属人和体检日期");
    return;
  }

  if (!selectedFile.value) {
    ElMessage.error("请先选择报告文件");
    return;
  }

  uploadLoading.value = true;
  errorMessage.value = "";

  try {
    const payload = new FormData();
    payload.append("owner_id", String(form.owner_id));
    payload.append("exam_date", form.exam_date);
    if (form.institution_id) payload.append("institution_id", String(form.institution_id));
    if (form.package_id) payload.append("package_id", String(form.package_id));
    payload.append("file", selectedFile.value);

    const { data } = await uploadRecordByOcr(payload);
    uploadResult.value = data;
    try {
      await loadIndicatorDicts();
    } catch {
      ElMessage.warning("OCR 解析成功，但指标字典刷新失败；已保留当前选项，请刷新页面后再确认入档");
    }
    mappingDraftRows.value = createOcrMappingRows(
      data?.ocr?.candidate_mappings || []
    );
    ElMessage.success("OCR解析完成");
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "上传解析失败";
  } finally {
    uploadLoading.value = false;
  }
};

const confirmParsedRecord = async () => {
  if (!uploadResult.value?.item?.id) {
    return;
  }

  const { mappings: confirmedMappings, invalidRows } = buildOcrConfirmedMappings(
    mappingDraftRows.value,
    indicatorDicts.value
  );
  if (invalidRows.length) {
    const first = invalidRows[0];
    ElMessage.error(
      `${first.label}：${first.reason}${invalidRows.length > 1 ? `；另有 ${invalidRows.length - 1} 项待修正` : ""}`
    );
    return;
  }

  confirmLoading.value = true;
  try {
    const requestPayload = mappingDraftRows.value.length
      ? { confirmed_mappings: confirmedMappings }
      : null;

    const { data } = await confirmRecord(uploadResult.value.item.id, requestPayload);
    uploadResult.value.item = data.item;
    const confirmedCount = data?.ocr?.confirmed_count;
    if (typeof confirmedCount === "number") {
      ElMessage.success(`档案已确认，入档 ${confirmedCount} 项指标`);
    } else {
      ElMessage.success("档案已确认");
    }
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "确认失败");
  } finally {
    confirmLoading.value = false;
  }
};

const goRecordDetail = (recordId) => {
  router.push({ name: "record-detail", params: { id: recordId } });
};

const goRecords = () => {
  router.push({ name: "records" });
};

const goTrends = () => {
  router.push({ name: "trends" });
};

const goFriends = () => {
  router.push({ name: "friends" });
};

const goCommentModeration = () => {
  router.push({ name: "comment-moderation" });
};

const goInstitutions = () => {
  router.push({ name: "institutions" });
};

const logout = () => {
  authStore.logout();
  router.push({ name: "login" });
};

onMounted(async () => {
  try {
    if (!authStore.user) {
      await authStore.fetchMe();
    }
    form.owner_id = authStore.user?.id || null;
    const ownerLoader = authStore.user?.role === "admin" ? loadAdminUsers() : loadFriends();
    await Promise.all([loadInstitutions(), ownerLoader, loadIndicatorDicts()]);
    await Promise.all(institutions.value.map((institution) => loadPackages(institution.id)));
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "页面初始化失败";
  }
});
</script>
