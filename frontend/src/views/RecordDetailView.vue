<template>
  <div class="home-shell">
    <el-card class="home-card">
      <template #header>
        <div class="top-bar">
          <span>档案详情与指标录入</span>
          <MainNavActions>
            <template #prefix>
              <el-button type="success" plain data-testid="record-ocr-button" @click="goOcrUpload">
                {{ record?.ocr_pending_confirmation ? "继续 OCR 确认" : "OCR 上传报告" }}
              </el-button>
              <el-button plain @click="goBack">返回档案列表</el-button>
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
        v-if="record?.ocr_pending_confirmation"
        title="这份档案有一份待确认的 OCR 报告，可点击“继续 OCR 确认”继续核对，或在核对页放弃暂存报告。"
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
      />

      <el-skeleton :rows="6" animated v-if="loading" />

      <template v-else>
        <el-descriptions :column="1" border style="margin-bottom: 16px">
          <el-descriptions-item label="档案ID">{{ formatRecordDisplayId(record) }}</el-descriptions-item>
          <el-descriptions-item label="档案归属人">{{ record?.owner?.username || '-' }}</el-descriptions-item>
          <el-descriptions-item label="上传人">{{ record?.uploader?.username || '-' }}</el-descriptions-item>
          <el-descriptions-item label="体检日期">{{ record?.exam_date || '-' }}</el-descriptions-item>
          <el-descriptions-item label="机构">
            {{ record?.institution ? `${record.institution.name} · ${record.institution.branch_name}` : '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="套餐">{{ record?.package?.name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ record?.status || '-' }}</el-descriptions-item>
        </el-descriptions>

        <el-card shadow="never" style="margin-bottom: 16px">
          <template #header>
            <span>新增指标</span>
          </template>

          <div class="indicator-form-grid">
            <el-select v-model="addForm.indicator_dict_id" filterable placeholder="选择指标" style="width: 100%">
              <el-option
                v-for="dict in indicatorDicts"
                :key="dict.id"
                :label="`${dict.code} - ${dict.name}`"
                :value="dict.id"
              >
                <span>{{ dict.code }} - {{ dict.name }}</span>
                <span class="indicator-option-reference">
                  {{ dict.reference_low ?? '-' }} ~ {{ dict.reference_high ?? '-' }} {{ dict.unit || '' }}
                </span>
              </el-option>
            </el-select>

            <el-input v-model="addForm.value" placeholder="输入指标值" />
            <el-button type="primary" :loading="addLoading" @click="addIndicator">添加指标</el-button>
          </div>
        </el-card>

        <el-table :data="recordIndicators" border empty-text="暂无指标数据">
          <el-table-column label="指标" min-width="200">
            <template #default="scope">
              {{ scope.row.indicator?.code }} - {{ scope.row.indicator?.name }}
            </template>
          </el-table-column>
          <el-table-column label="值" min-width="120">
            <template #default="scope">
              {{ scope.row.value }} {{ scope.row.indicator?.unit || '' }}
            </template>
          </el-table-column>
          <el-table-column label="参考范围" min-width="140">
            <template #default="scope">
              {{ scope.row.indicator?.reference_low ?? '-' }} ~ {{ scope.row.indicator?.reference_high ?? '-' }}
            </template>
          </el-table-column>
          <el-table-column label="是否异常" width="110">
            <template #default="scope">
              <el-tag :type="scope.row.is_abnormal ? 'danger' : 'success'">
                {{ scope.row.is_abnormal ? '异常' : '正常' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="170" fixed="right">
            <template #default="scope">
              <el-button type="primary" link @click="editIndicator(scope.row)">修改</el-button>
              <el-button type="danger" link @click="removeIndicator(scope.row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useRoute, useRouter } from "vue-router";

import MainNavActions from "../components/MainNavActions.vue";
import { fetchIndicatorDicts } from "../api/indicators";
import {
  addRecordIndicator,
  deleteRecordIndicator,
  fetchRecordDetail,
  updateRecordIndicator,
} from "../api/records";
import { useAuthStore } from "../stores/auth";
import { formatRecordDisplayId } from "../utils/recordDisplayId";

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();

const loading = ref(false);
const addLoading = ref(false);
const errorMessage = ref("");
const record = ref(null);
const indicatorDicts = ref([]);

const addForm = reactive({
  indicator_dict_id: null,
  value: "",
});

const recordId = computed(() => route.params.id);

const recordIndicators = computed(() => record.value?.indicators || []);

const loadIndicatorDicts = async () => {
  const { data } = await fetchIndicatorDicts();
  indicatorDicts.value = data.items || [];
};

const loadRecord = async () => {
  loading.value = true;
  errorMessage.value = "";

  try {
    const { data } = await fetchRecordDetail(recordId.value);
    record.value = data.item;
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "档案详情加载失败";
    record.value = null;
  } finally {
    loading.value = false;
  }
};

const addIndicator = async () => {
  if (!addForm.indicator_dict_id || !addForm.value) {
    ElMessage.error("请选择指标并填写值");
    return;
  }

  addLoading.value = true;

  try {
    await addRecordIndicator(recordId.value, {
      indicator_dict_id: addForm.indicator_dict_id,
      value: addForm.value,
    });
    addForm.indicator_dict_id = null;
    addForm.value = "";
    ElMessage.success("指标添加成功");
    await loadRecord();
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "指标添加失败");
  } finally {
    addLoading.value = false;
  }
};

const editIndicator = async (indicatorRow) => {
  try {
    const { value } = await ElMessageBox.prompt("请输入新的指标值", "修改指标", {
      inputValue: indicatorRow.value,
      confirmButtonText: "保存",
      cancelButtonText: "取消",
      inputPattern: /.+/,
      inputErrorMessage: "指标值不能为空",
    });

    await updateRecordIndicator(recordId.value, indicatorRow.id, {
      value,
    });
    ElMessage.success("指标已更新");
    await loadRecord();
  } catch (error) {
    if (error === "cancel") {
      return;
    }
    ElMessage.error(error?.response?.data?.message || "指标修改失败");
  }
};

const removeIndicator = async (indicatorRow) => {
  try {
    await ElMessageBox.confirm("确认删除该指标吗？", "提示", {
      type: "warning",
      confirmButtonText: "确认删除",
      cancelButtonText: "取消",
    });

    await deleteRecordIndicator(recordId.value, indicatorRow.id);
    ElMessage.success("指标已删除");
    await loadRecord();
  } catch (error) {
    if (error === "cancel") {
      return;
    }
    ElMessage.error(error?.response?.data?.message || "指标删除失败");
  }
};

const goBack = () => {
  router.push({ name: "records" });
};

const goOcrUpload = () => {
  router.push({
    name: "record-upload",
    query: { record_id: String(recordId.value) },
  });
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
  await Promise.all([loadIndicatorDicts(), loadRecord()]);
});
</script>
