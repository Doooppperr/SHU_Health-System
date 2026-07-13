<template>
  <div class="home-shell">
    <el-card class="home-card">
      <template #header>
        <div class="top-bar">
          <span>体检档案列表</span>
          <MainNavActions>
            <template #prefix>
              <el-button
                v-if="authStore.user?.role === 'user'"
                type="success"
                :disabled="selectedRecords.length === 0 || aiStore.isSending"
                :loading="aiStore.isSending"
                data-testid="record-analysis-button"
                @click="prepareAnalysis"
              >
                智能分析（{{ selectedRecords.length }}）
              </el-button>
              <el-button
                v-if="authStore.user?.role === 'user'"
                plain
                data-testid="new-record-ocr-button"
                @click="goUpload()"
              >
                OCR 上传报告
              </el-button>
              <el-button type="primary" @click="openCreateDialog">新建档案</el-button>
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

      <el-table
        ref="recordTableRef"
        :data="records"
        row-key="id"
        border
        v-loading="loading"
        empty-text="暂无档案，请先创建"
        @selection-change="handleSelectionChange"
      >
        <el-table-column
          v-if="authStore.user?.role === 'user'"
          type="selection"
          width="55"
          :selectable="isRecordSelectable"
          :reserve-selection="false"
        />
        <el-table-column label="档案ID" width="120">
          <template #default="scope">{{ formatRecordDisplayId(scope.row) }}</template>
        </el-table-column>
        <el-table-column prop="exam_date" label="体检日期" width="130" />
        <el-table-column label="档案归属人" min-width="140">
          <template #default="scope">
            <span>{{ scope.row.owner?.username || "-" }}</span>
          </template>
        </el-table-column>
        <el-table-column label="机构" min-width="240">
          <template #default="scope">
            <span>{{ scope.row.institution ? `${scope.row.institution.name} · ${scope.row.institution.branch_name}` : '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="套餐" min-width="180">
          <template #default="scope">
            <span>{{ scope.row.package?.name || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="110" />
        <el-table-column prop="indicator_count" label="指标数" width="90" />
        <el-table-column label="操作" width="350" fixed="right">
          <template #default="scope">
            <el-button type="primary" link @click="goDetail(scope.row.id)">录入指标</el-button>
            <el-button
              v-if="authStore.user?.role === 'user'"
              type="success"
              link
              @click="goUpload(scope.row)"
            >
              {{ scope.row.ocr_pending_confirmation ? "继续OCR确认" : "OCR录入" }}
            </el-button>
            <el-button type="primary" link @click="openEditDialog(scope.row)">修改档案</el-button>
            <el-button type="danger" link @click="removeRecord(scope.row.id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="createDialogVisible" title="新建体检档案" width="560px" :close-on-click-modal="false">
      <el-form label-width="96px" :model="createForm">
        <el-form-item label="体检日期" required>
          <el-date-picker
            v-model="createForm.exam_date"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="请选择体检日期"
            style="width: 100%"
          />
        </el-form-item>

        <el-form-item label="档案归属人" required>
          <el-select v-model="createForm.owner_id" placeholder="请选择档案归属人" style="width: 100%">
            <el-option
              v-for="owner in ownerOptions"
              :key="owner.id"
              :label="owner.label"
              :value="owner.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="体检机构（可选）">
          <el-select
            v-model="createForm.institution_id"
            clearable
            placeholder="暂不选取"
            style="width: 100%"
            @change="onInstitutionChange"
          >
            <el-option
              v-for="institution in institutions"
              :key="institution.id"
              :label="`${institution.name} · ${institution.branch_name}`"
              :value="institution.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="体检套餐（可选）">
          <el-select v-model="createForm.package_id" clearable placeholder="暂不选取" style="width: 100%" @change="onCreatePackageChange">
            <el-option v-for="pkg in currentPackages" :key="pkg.id" :label="packageLabel(pkg, createForm.institution_id)" :value="pkg.id" />
          </el-select>
        </el-form-item>

        <el-alert
          v-if="createForm.institution_id"
          title="档案确认后，标准化档案信息和指标会自动向对应机构管理员只读开放。联系方式和原始报告不会开放。"
          type="warning"
          show-icon
          :closable="false"
        />
      </el-form>

      <template #footer>
        <el-button @click="createDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="createLoading" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="editDialogVisible" title="修改体检档案" width="560px" :close-on-click-modal="false">
      <el-form label-width="96px" :model="editForm">
        <el-form-item label="体检日期" required>
          <el-date-picker
            v-model="editForm.exam_date"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="请选择体检日期"
            style="width: 100%"
          />
        </el-form-item>

        <el-form-item label="档案归属人" required>
          <el-select v-model="editForm.owner_id" disabled placeholder="档案归属创建后不可修改" style="width: 100%">
            <el-option
              v-for="owner in ownerOptions"
              :key="owner.id"
              :label="owner.label"
              :value="owner.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="体检机构（可选）">
          <el-select
            v-model="editForm.institution_id"
            clearable
            placeholder="暂不选取"
            style="width: 100%"
            @change="onEditInstitutionChange"
          >
            <el-option
              v-for="institution in institutions"
              :key="institution.id"
              :label="`${institution.name} · ${institution.branch_name}`"
              :value="institution.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="体检套餐（可选）">
          <el-select v-model="editForm.package_id" clearable placeholder="暂不选取" style="width: 100%" @change="onEditPackageChange">
            <el-option v-for="pkg in currentEditPackages" :key="pkg.id" :label="packageLabel(pkg, editForm.institution_id)" :value="pkg.id" />
          </el-select>
        </el-form-item>

        <el-alert
          v-if="editForm.institution_id"
          title="已确认档案会自动向所选机构管理员开放标准化数据。清空机构后，原机构将立即失去访问权。"
          type="warning"
          show-icon
          :closable="false"
          style="margin-bottom: 16px"
        />

        <el-form-item label="状态" required>
          <el-select v-model="editForm.status" style="width: 100%">
            <el-option label="draft" value="draft" />
            <el-option label="parsed" value="parsed" />
            <el-option label="confirmed" value="confirmed" />
          </el-select>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="editDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="editLoading" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useRouter } from "vue-router";

import MainNavActions from "../components/MainNavActions.vue";
import { fetchFriends } from "../api/friends";
import { fetchInstitutions, fetchInstitutionPackages } from "../api/institutions";
import { createRecord, deleteRecord, fetchRecords, updateRecord } from "../api/records";
import { fetchUsers } from "../api/users";
import { useAiChatStore } from "../stores/aiChat";
import { useAuthStore } from "../stores/auth";
import { formatRecordDisplayId } from "../utils/recordDisplayId";

const router = useRouter();
const authStore = useAuthStore();
const aiStore = useAiChatStore();

const loading = ref(false);
const errorMessage = ref("");
const records = ref([]);
const recordTableRef = ref(null);
const selectedRecords = ref([]);
let synchronizingSelection = false;

const institutions = ref([]);
const packageMap = ref({});
const manageableFriends = ref([]);
const adminUsers = ref([]);

const createDialogVisible = ref(false);
const createLoading = ref(false);
const createForm = reactive({
  exam_date: "",
  owner_id: null,
  institution_id: null,
  package_id: null,
});
const editDialogVisible = ref(false);
const editLoading = ref(false);
const editForm = reactive({
  id: null,
  exam_date: "",
  owner_id: null,
  institution_id: null,
  package_id: null,
  status: "confirmed",
});

const currentPackages = computed(() => {
  if (createForm.institution_id) return packageMap.value[createForm.institution_id] || [];
  return Object.values(packageMap.value).flat();
});

const currentEditPackages = computed(() => {
  if (editForm.institution_id) return packageMap.value[editForm.institution_id] || [];
  return Object.values(packageMap.value).flat();
});

const packageLabel = (pkg, selectedInstitutionId) => {
  if (selectedInstitutionId) return pkg.name;
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

const selectedOwnerId = computed(() => selectedRecords.value[0]?.owner_id ?? null);

const isRecordAnalyzable = (record) => (
  record?.status === "confirmed" && Number(record?.indicator_count) > 0
);

const isRecordSelectable = (record) => {
  if (!isRecordAnalyzable(record)) {
    return false;
  }

  return selectedOwnerId.value === null || record.owner_id === selectedOwnerId.value;
};

const handleSelectionChange = (selection) => {
  if (synchronizingSelection) {
    return;
  }

  const analyzableSelection = selection.filter(isRecordAnalyzable);
  const ownerId = selectedOwnerId.value ?? analyzableSelection[0]?.owner_id ?? null;
  const normalizedSelection = ownerId === null
    ? []
    : analyzableSelection.filter((record) => record.owner_id === ownerId);
  selectedRecords.value = normalizedSelection;

  if (normalizedSelection.length !== selection.length && recordTableRef.value) {
    synchronizingSelection = true;
    recordTableRef.value.clearSelection?.();
    normalizedSelection.forEach((record) => {
      recordTableRef.value?.toggleRowSelection?.(record, true);
    });
    synchronizingSelection = false;
  }
};

const clearAnalysisSelection = () => {
  selectedRecords.value = [];
  recordTableRef.value?.clearSelection?.();
};

const prepareAnalysis = () => {
  if (selectedRecords.value.length === 0 || aiStore.isSending) {
    return;
  }

  aiStore.prepareRecordAnalysis([...selectedRecords.value]);
};

const loadRecords = async () => {
  loading.value = true;
  errorMessage.value = "";

  try {
    const { data } = await fetchRecords();
    records.value = data.items || [];
    clearAnalysisSelection();
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "档案列表加载失败";
  } finally {
    loading.value = false;
  }
};

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

const openCreateDialog = async () => {
  createDialogVisible.value = true;
  createForm.owner_id = authStore.user?.id || null;

  if (institutions.value.length === 0) {
    try {
      await loadInstitutions();
    } catch (error) {
      errorMessage.value = error?.response?.data?.message || "机构信息加载失败";
    }
  }

  if (authStore.user?.role === "admin") {
    if (adminUsers.value.length === 0) {
      try {
        await loadAdminUsers();
      } catch (error) {
        errorMessage.value = error?.response?.data?.message || "用户列表加载失败";
      }
    }
  } else if (manageableFriends.value.length === 0) {
    try {
      await loadFriends();
    } catch (error) {
      errorMessage.value = error?.response?.data?.message || "亲友关系加载失败";
    }
  }
};

const onInstitutionChange = async (institutionId) => {
  createForm.package_id = null;
  if (!institutionId) return;
  try {
    await loadPackages(institutionId);
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "套餐信息加载失败";
  }
};

const resetCreateForm = () => {
  createForm.exam_date = "";
  createForm.owner_id = authStore.user?.id || null;
  createForm.institution_id = null;
  createForm.package_id = null;
};

const onCreatePackageChange = (packageId) => {
  if (!packageId) return;
  const pkg = Object.values(packageMap.value).flat().find((item) => item.id === packageId);
  if (pkg?.institution_id && createForm.institution_id !== pkg.institution_id) {
    createForm.institution_id = pkg.institution_id;
  }
};

const openEditDialog = async (row) => {
  editForm.id = row.id;
  editForm.exam_date = row.exam_date || "";
  editForm.owner_id = row.owner_id;
  editForm.institution_id = row.institution_id;
  editForm.package_id = row.package_id;
  editForm.status = row.status || "confirmed";

  if (row.institution_id) {
    try {
      await loadPackages(row.institution_id);
    } catch (error) {
      errorMessage.value = error?.response?.data?.message || "套餐信息加载失败";
      return;
    }
  }

  editDialogVisible.value = true;
};

const onEditInstitutionChange = async (institutionId) => {
  editForm.package_id = null;
  if (!institutionId) return;
  try {
    await loadPackages(institutionId);
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "套餐信息加载失败";
  }
};

const onEditPackageChange = (packageId) => {
  if (!packageId) return;
  const pkg = Object.values(packageMap.value).flat().find((item) => item.id === packageId);
  if (pkg?.institution_id && editForm.institution_id !== pkg.institution_id) {
    editForm.institution_id = pkg.institution_id;
  }
};

const submitEdit = async () => {
  if (!editForm.id || !editForm.exam_date || !editForm.owner_id) {
    ElMessage.error("请完整填写体检日期和档案归属人");
    return;
  }

  editLoading.value = true;

  try {
    await updateRecord(editForm.id, {
      exam_date: editForm.exam_date,
      owner_id: editForm.owner_id,
      institution_id: editForm.institution_id,
      package_id: editForm.package_id,
      status: editForm.status,
    });
    ElMessage.success("档案已更新");
    editDialogVisible.value = false;
    await loadRecords();
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "档案更新失败");
  } finally {
    editLoading.value = false;
  }
};

const submitCreate = async () => {
  if (!createForm.exam_date || !createForm.owner_id) {
    ElMessage.error("请完整填写体检日期和档案归属人");
    return;
  }

  createLoading.value = true;

  try {
    await createRecord({
      exam_date: createForm.exam_date,
      owner_id: createForm.owner_id,
      institution_id: createForm.institution_id,
      package_id: createForm.package_id,
      status: "confirmed",
    });
    ElMessage.success("档案创建成功，可选择手工录入或 OCR 录入");
    createDialogVisible.value = false;
    resetCreateForm();
    await loadRecords();
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "档案创建失败");
  } finally {
    createLoading.value = false;
  }
};

const goDetail = (recordId) => {
  router.push({ name: "record-detail", params: { id: recordId } });
};

const goUpload = (record = null) => {
  if (record?.id) {
    router.push({
      name: "record-upload",
      query: { record_id: String(record.id) },
    });
    return;
  }
  router.push({ name: "record-upload" });
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

const removeRecord = async (recordId) => {
  try {
    await ElMessageBox.confirm("删除后无法恢复，确认删除该档案？", "提示", {
      type: "warning",
      confirmButtonText: "确认删除",
      cancelButtonText: "取消",
    });

    await deleteRecord(recordId);
    ElMessage.success("档案已删除");
    await loadRecords();
  } catch (error) {
    if (error === "cancel") {
      return;
    }
    ElMessage.error(error?.response?.data?.message || "删除失败");
  }
};

const goInstitutions = () => {
  router.push({ name: "institutions" });
};

const goProfile = () => {
  router.push({ name: "profile" });
};

const logout = () => {
  authStore.logout();
  router.push({ name: "login" });
};

onMounted(async () => {
  if (!authStore.user) {
    try {
      await authStore.fetchMe();
    } catch (error) {
      errorMessage.value = error?.response?.data?.message || "用户信息加载失败";
    }
  }

  const ownerLoader = authStore.user?.role === "admin" ? loadAdminUsers() : loadFriends();
  await Promise.all([loadRecords(), loadInstitutions(), ownerLoader]);
  await Promise.all(institutions.value.map((institution) => loadPackages(institution.id)));
  if (!createForm.owner_id && authStore.user) {
    createForm.owner_id = authStore.user.id;
  }
});
</script>
