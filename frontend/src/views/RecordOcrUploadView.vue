<template>
  <div class="home-shell">
    <el-card class="home-card">
      <template #header>
        <div class="top-bar">
          <span>OCR上传解析</span>
          <MainNavActions>
            <template #prefix>
              <el-button plain @click="goBack">
                {{ isAttachMode ? "返回档案详情" : "返回档案列表" }}
              </el-button>
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

      <el-alert
        v-if="isAttachMode && targetRecord"
        :title="`报告将录入现有档案 ${formatRecordDisplayId(targetRecord)}`"
        description="不会创建重复档案。确认前原档案状态、原报告和已有指标保持不变；确认入档时，OCR 会更新同名指标并保留报告中未涉及的已有指标。"
        type="info"
        show-icon
        :closable="false"
        style="margin-bottom: 16px"
      />

      <el-descriptions
        v-if="isAttachMode && targetRecord"
        :column="1"
        border
        style="margin-bottom: 16px"
        data-testid="ocr-target-record"
      >
        <el-descriptions-item label="目标档案">{{ formatRecordDisplayId(targetRecord) }}</el-descriptions-item>
        <el-descriptions-item label="档案归属人">{{ targetRecord.owner?.username || "-" }}</el-descriptions-item>
        <el-descriptions-item label="体检日期">{{ targetRecord.exam_date || "-" }}</el-descriptions-item>
        <el-descriptions-item label="已有指标">{{ targetRecord.indicator_count || 0 }} 项</el-descriptions-item>
      </el-descriptions>

      <el-form label-width="100px" :model="form" class="ocr-form">
        <el-form-item v-if="!isAttachMode" label="档案归属人" required>
          <el-select v-model="form.owner_id" placeholder="请选择档案归属人" style="width: 100%">
            <el-option
              v-for="owner in ownerOptions"
              :key="owner.id"
              :label="owner.label"
              :value="owner.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item v-if="!isAttachMode" label="体检日期" required>
          <el-date-picker
            v-model="form.exam_date"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="请选择体检日期"
            style="width: 100%"
          />
        </el-form-item>

        <el-form-item v-if="!isAttachMode" label="体检机构（可选）">
          <el-select v-model="form.institution_id" clearable placeholder="暂不选取" style="width: 100%" @change="onInstitutionChange">
            <el-option
              v-for="institution in institutions"
              :key="institution.id"
              :label="`${institution.name} · ${institution.branch_name}`"
              :value="institution.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item v-if="!isAttachMode" label="体检套餐（可选）">
          <el-select v-model="form.package_id" clearable placeholder="暂不选取" style="width: 100%" @change="onPackageChange">
            <el-option v-for="pkg in currentPackages" :key="pkg.id" :label="packageLabel(pkg)" :value="pkg.id" />
          </el-select>
        </el-form-item>

        <el-form-item v-if="!isAttachMode && form.institution_id">
          <el-alert
            title="确认入档后，标准化档案信息和指标会自动向对应机构管理员只读开放。联系方式和原始报告不会开放。"
            type="warning"
            show-icon
            :closable="false"
          />
        </el-form-item>

        <el-form-item v-else-if="!isAttachMode">
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
          <el-button
            type="primary"
            :loading="uploadLoading"
            :disabled="(isAttachMode && !targetRecord) || confirmLoading || cancelLoading"
            @click="submitUpload"
          >
            上传并解析
          </el-button>
        </el-form-item>
      </el-form>

      <el-card v-if="uploadResult" shadow="never" style="margin-top: 18px">
        <template #header>
          <div class="top-bar">
            <span>OCR解析结果</span>
            <div class="top-actions wrap-actions">
              <el-tag :type="uploadNeedsConfirmation ? 'warning' : 'success'">
                {{ uploadNeedsConfirmation ? "待确认" : uploadResult.item.status }}
              </el-tag>
              <el-button
                v-if="uploadNeedsConfirmation"
                type="success"
                :loading="confirmLoading"
                :disabled="uploadLoading || cancelLoading"
                @click="confirmParsedRecord"
              >
                确认入档
              </el-button>
              <el-button
                v-if="uploadResult.ocr?.pending_confirmation"
                type="danger"
                plain
                :loading="cancelLoading"
                :disabled="uploadLoading || confirmLoading"
                @click="cancelPendingOcr"
              >
                放弃暂存报告
              </el-button>
              <el-button type="primary" plain @click="goRecordDetail(uploadResult.item.id)">去修正指标</el-button>
            </div>
          </div>
        </template>

        <el-descriptions :column="1" border style="margin-bottom: 12px">
          <el-descriptions-item label="档案ID">{{ formatRecordDisplayId(uploadResult.item) }}</el-descriptions-item>
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
import { computed, onMounted, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useRoute, useRouter } from "vue-router";

import MainNavActions from "../components/MainNavActions.vue";
import { fetchFriends } from "../api/friends";
import { fetchIndicatorDicts } from "../api/indicators";
import { fetchInstitutions, fetchInstitutionPackages } from "../api/institutions";
import {
  cancelPendingRecordOcr,
  confirmRecord,
  fetchPendingRecordOcr,
  fetchRecordDetail,
  uploadRecordByOcr,
} from "../api/records";
import { fetchUsers } from "../api/users";
import { useAuthStore } from "../stores/auth";
import { formatRecordDisplayId } from "../utils/recordDisplayId";
import {
  buildOcrConfirmedMappings,
  createOcrMappingRows,
} from "../utils/ocrConfirmation";

const route = useRoute();
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
const cancelLoading = ref(false);
const errorMessage = ref("");
const uploadResult = ref(null);
const targetRecord = ref(null);

const form = reactive({
  owner_id: null,
  exam_date: "",
  institution_id: null,
  package_id: null,
});

const rawTargetRecordId = computed(() => (
  Array.isArray(route.query.record_id)
    ? route.query.record_id[0]
    : route.query.record_id
));

const isAttachMode = computed(() => (
  rawTargetRecordId.value !== undefined
  && rawTargetRecordId.value !== null
  && rawTargetRecordId.value !== ""
));

const targetRecordId = computed(() => {
  const rawValue = rawTargetRecordId.value;
  const parsedValue = Number(rawValue);
  return Number.isInteger(parsedValue) && parsedValue > 0 ? parsedValue : null;
});

const uploadNeedsConfirmation = computed(() => Boolean(
  uploadResult.value
  && (
    uploadResult.value.item?.status !== "confirmed"
    || uploadResult.value.ocr?.pending_confirmation
  )
));

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

let targetLoadSequence = 0;
let uploadSequence = 0;
let pendingActionSequence = 0;
let targetContextVersion = 0;

const applyOcrResult = (data) => {
  uploadResult.value = data;
  mappingDraftRows.value = createOcrMappingRows(
    data?.ocr?.candidate_mappings || []
  );
};

const resetTargetUploadState = () => {
  targetLoadSequence += 1;
  uploadSequence += 1;
  pendingActionSequence += 1;
  targetContextVersion += 1;
  targetRecord.value = null;
  selectedFile.value = null;
  fileList.value = [];
  uploadResult.value = null;
  mappingDraftRows.value = [];
  errorMessage.value = "";
  uploadLoading.value = false;
  confirmLoading.value = false;
  cancelLoading.value = false;
};

const loadTargetRecord = async () => {
  const loadSequence = ++targetLoadSequence;
  if (!isAttachMode.value) {
    return;
  }
  const requestedRecordId = targetRecordId.value;
  if (!requestedRecordId) {
    errorMessage.value = "目标档案编号无效，请返回档案列表后重新选择";
    return;
  }
  const { data } = await fetchRecordDetail(requestedRecordId);
  if (loadSequence !== targetLoadSequence || targetRecordId.value !== requestedRecordId) {
    return;
  }
  targetRecord.value = data.item;
  form.owner_id = data.item.owner_id;
  form.exam_date = data.item.exam_date || "";
  form.institution_id = data.item.institution_id;
  form.package_id = data.item.package_id;
  if (data.item.ocr_pending_confirmation) {
    try {
      const pendingResponse = await fetchPendingRecordOcr(requestedRecordId);
      if (loadSequence !== targetLoadSequence || targetRecordId.value !== requestedRecordId) {
        return;
      }
      applyOcrResult(pendingResponse.data);
    } catch (error) {
      if (error?.response?.status !== 404) throw error;
      targetRecord.value = { ...data.item, ocr_pending_confirmation: false };
    }
  }
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
  if (
    isAttachMode.value
    && (
      !targetRecordId.value
      || !targetRecord.value
      || Number(targetRecord.value.id) !== targetRecordId.value
    )
  ) {
    ElMessage.error("目标档案加载失败，请返回档案列表后重新选择");
    return;
  }

  if (!isAttachMode.value && (!form.owner_id || !form.exam_date)) {
    ElMessage.error("请先填写档案归属人和体检日期");
    return;
  }

  if (!selectedFile.value) {
    ElMessage.error("请先选择报告文件");
    return;
  }

  const operationSequence = ++uploadSequence;
  targetLoadSequence += 1;
  pendingActionSequence += 1;
  const contextVersion = targetContextVersion;
  const attaching = isAttachMode.value;
  const submittedTargetId = attaching ? targetRecordId.value : null;
  uploadLoading.value = true;
  confirmLoading.value = false;
  cancelLoading.value = false;
  errorMessage.value = "";

  try {
    const payload = new FormData();
    if (attaching) {
      payload.append("record_id", String(submittedTargetId));
    } else {
      payload.append("owner_id", String(form.owner_id));
      payload.append("exam_date", form.exam_date);
      if (form.institution_id) payload.append("institution_id", String(form.institution_id));
      if (form.package_id) payload.append("package_id", String(form.package_id));
    }
    payload.append("file", selectedFile.value);

    const { data } = await uploadRecordByOcr(payload);
    let dictionaryRefreshFailed = false;
    try {
      await loadIndicatorDicts();
    } catch {
      dictionaryRefreshFailed = true;
    }

    if (
      operationSequence !== uploadSequence
      || contextVersion !== targetContextVersion
      || (attaching && targetRecordId.value !== submittedTargetId)
    ) {
      return;
    }

    applyOcrResult(data);
    if (attaching) {
      targetRecord.value = data.item;
    }
    if (dictionaryRefreshFailed) {
      ElMessage.warning("OCR 解析成功，但指标字典刷新失败；已保留当前选项，请刷新页面后再确认入档");
    }
    ElMessage.success(
      attaching
        ? "报告已解析，确认入档前原档案保持不变"
        : "OCR解析完成"
    );
  } catch (error) {
    if (
      operationSequence === uploadSequence
      && contextVersion === targetContextVersion
      && (!attaching || targetRecordId.value === submittedTargetId)
    ) {
      errorMessage.value = error?.response?.data?.message || "上传解析失败";
    }
  } finally {
    if (
      operationSequence === uploadSequence
      && contextVersion === targetContextVersion
    ) {
      uploadLoading.value = false;
    }
  }
};

const confirmParsedRecord = async () => {
  const resultToConfirm = uploadResult.value;
  if (!resultToConfirm?.item?.id) {
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

  const attachmentId = resultToConfirm.ocr?.pending_confirmation
    ? resultToConfirm.ocr?.attachment_id
    : null;
  if (resultToConfirm.ocr?.pending_confirmation && !attachmentId) {
    ElMessage.error("暂存 OCR 版本无效，请刷新页面后重试");
    return;
  }

  const actionSequence = ++pendingActionSequence;
  const contextVersion = targetContextVersion;
  const submittedTargetId = isAttachMode.value ? targetRecordId.value : null;
  const recordId = resultToConfirm.item.id;
  confirmLoading.value = true;
  try {
    const requestPayload = {};
    if (mappingDraftRows.value.length) {
      requestPayload.confirmed_mappings = confirmedMappings;
    }
    if (attachmentId) {
      requestPayload.attachment_id = attachmentId;
    }

    const { data } = await confirmRecord(
      recordId,
      Object.keys(requestPayload).length ? requestPayload : null
    );
    if (
      actionSequence !== pendingActionSequence
      || contextVersion !== targetContextVersion
      || (submittedTargetId && targetRecordId.value !== submittedTargetId)
      || (attachmentId && uploadResult.value?.ocr?.attachment_id !== attachmentId)
    ) {
      return;
    }
    uploadResult.value = {
      ...uploadResult.value,
      item: data.item,
      ocr: {
        ...uploadResult.value.ocr,
        ...data.ocr,
        pending_confirmation: false,
      },
    };
    if (isAttachMode.value) {
      targetRecord.value = data.item;
    }
    const confirmedCount = data?.ocr?.confirmed_count;
    if (typeof confirmedCount === "number") {
      ElMessage.success(`档案已确认，入档 ${confirmedCount} 项指标`);
    } else {
      ElMessage.success("档案已确认");
    }
  } catch (error) {
    if (
      actionSequence === pendingActionSequence
      && contextVersion === targetContextVersion
      && (!submittedTargetId || targetRecordId.value === submittedTargetId)
    ) {
      ElMessage.error(error?.response?.data?.message || "确认失败");
      if (error?.response?.status === 409 && submittedTargetId) {
        await loadTargetRecord();
      }
    }
  } finally {
    if (
      actionSequence === pendingActionSequence
      && contextVersion === targetContextVersion
    ) {
      confirmLoading.value = false;
    }
  }
};

const cancelPendingOcr = async () => {
  const pendingResult = uploadResult.value;
  const recordId = pendingResult?.item?.id;
  const attachmentId = pendingResult?.ocr?.attachment_id;
  if (!recordId || !attachmentId || !pendingResult?.ocr?.pending_confirmation) {
    return;
  }

  const promptActionSequence = pendingActionSequence;
  const promptContextVersion = targetContextVersion;
  const submittedTargetId = isAttachMode.value ? targetRecordId.value : null;

  try {
    await ElMessageBox.confirm(
      "放弃后会删除本次暂存报告，原档案、原报告和已有指标不会变化。",
      "放弃暂存报告",
      {
        type: "warning",
        confirmButtonText: "确认放弃",
        cancelButtonText: "继续核对",
      }
    );
  } catch (error) {
    if (error === "cancel" || error === "close") return;
    throw error;
  }

  if (
    promptActionSequence !== pendingActionSequence
    || promptContextVersion !== targetContextVersion
    || (submittedTargetId && targetRecordId.value !== submittedTargetId)
    || uploadResult.value?.item?.id !== recordId
    || uploadResult.value?.ocr?.attachment_id !== attachmentId
    || !uploadResult.value?.ocr?.pending_confirmation
  ) {
    return;
  }

  const actionSequence = ++pendingActionSequence;
  const contextVersion = promptContextVersion;
  cancelLoading.value = true;
  try {
    const { data } = await cancelPendingRecordOcr(recordId, attachmentId);
    if (
      actionSequence !== pendingActionSequence
      || contextVersion !== targetContextVersion
      || (submittedTargetId && targetRecordId.value !== submittedTargetId)
    ) {
      return;
    }
    targetRecord.value = data.item;
    uploadResult.value = null;
    mappingDraftRows.value = [];
    selectedFile.value = null;
    fileList.value = [];
    ElMessage.success("已放弃暂存报告，原档案保持不变");
  } catch (error) {
    if (
      actionSequence === pendingActionSequence
      && contextVersion === targetContextVersion
      && (!submittedTargetId || targetRecordId.value === submittedTargetId)
    ) {
      ElMessage.error(error?.response?.data?.message || "放弃暂存报告失败");
      if (error?.response?.status === 409 && submittedTargetId) {
        await loadTargetRecord();
      }
    }
  } finally {
    if (
      actionSequence === pendingActionSequence
      && contextVersion === targetContextVersion
    ) {
      cancelLoading.value = false;
    }
  }
};

const goRecordDetail = (recordId) => {
  router.push({ name: "record-detail", params: { id: recordId } });
};

const goRecords = () => {
  router.push({ name: "records" });
};

const goBack = () => {
  if (isAttachMode.value && targetRecordId.value) {
    router.push({ name: "record-detail", params: { id: targetRecordId.value } });
    return;
  }
  goRecords();
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

watch(targetRecordId, async (recordId, previousRecordId) => {
  if (recordId === previousRecordId) return;
  resetTargetUploadState();
  if (!isAttachMode.value) return;
  try {
    await loadTargetRecord();
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "目标档案加载失败";
  }
});

onMounted(async () => {
  try {
    if (!authStore.user) {
      await authStore.fetchMe();
    }
    form.owner_id = authStore.user?.id || null;
    const ownerLoader = authStore.user?.role === "admin" ? loadAdminUsers() : loadFriends();
    await Promise.all([
      loadInstitutions(),
      ownerLoader,
      loadIndicatorDicts(),
      loadTargetRecord(),
    ]);
    await Promise.all(institutions.value.map((institution) => loadPackages(institution.id)));
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "页面初始化失败";
  }
});
</script>
