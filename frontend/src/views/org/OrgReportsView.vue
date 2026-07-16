<template>
  <div class="workspace-page">
    <section class="page-intro">
      <div>
        <p>机构数据生产</p>
        <h2>体检报告</h2>
        <span>草稿可编辑；确认锁定后内容不可再修改，提交归档后不可撤回。</span>
      </div>
      <div>
        <el-button @click="ocrVisible = true">OCR 录入</el-button>
        <el-button type="primary" @click="createVisible = true">手工新建</el-button>
      </div>
    </section>

    <el-alert
      title="报告机构由当前账号的服务端绑定关系确定；提交后按健康身份码和姓名永久归档给注册用户，不可撤下。"
      type="info"
      show-icon
      :closable="false"
    />

    <el-card shadow="never">
      <el-select v-model="status" @change="load">
        <el-option label="全部状态" value="" />
        <el-option v-for="item in statuses" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <el-table :data="items" v-loading="loading">
        <el-table-column prop="display_id" label="编号" />
        <el-table-column prop="subject_name_snapshot" label="受检者姓名" />
        <el-table-column prop="subject_health_id" label="健康身份码" />
        <el-table-column prop="exam_date" label="体检日期" />
        <el-table-column label="状态">
          <template #default="scope"><el-tag>{{ label(scope.row.status) }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="indicator_count" label="指标数" />
        <el-table-column label="操作" width="330">
          <template #default="scope">
            <el-button link @click="openDetail(scope.row)">查看</el-button>
            <el-button v-if="scope.row.status === 'draft'" link type="success" @click="lock(scope.row)">确认锁定</el-button>
            <el-button v-if="scope.row.status === 'locked'" link type="primary" @click="submit(scope.row)">提交并归档</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="createVisible" title="新建机构报告" width="600px">
      <ReportIdentityForm :form="form" :packages="packages" />
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="create">创建草稿</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="ocrVisible" title="OCR 录入临时报告" width="620px">
      <el-alert title="OCR 只生成待复核草稿；确认锁定时原文件会删除。" type="warning" show-icon :closable="false" />
      <input style="margin: 16px 0" type="file" accept=".pdf,.png,.jpg,.jpeg,.webp" @change="ocrFile = $event.target.files?.[0] || null" />
      <ReportIdentityForm :form="form" :packages="packages" />
      <template #footer>
        <el-button @click="ocrVisible = false">取消</el-button>
        <el-button type="primary" :loading="ocrLoading" @click="runOcr">解析并创建草稿</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="报告详情" size="min(850px,96vw)">
      <template v-if="current">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="姓名">{{ current.subject_name_snapshot }}</el-descriptions-item>
          <el-descriptions-item label="健康身份码">{{ current.subject_health_id }}</el-descriptions-item>
          <el-descriptions-item label="日期">{{ current.exam_date }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ label(current.status) }}</el-descriptions-item>
        </el-descriptions>
        <el-form v-if="current.status === 'draft'" inline style="margin-top: 18px">
          <el-form-item label="标准指标">
            <el-select v-model="indicatorForm.indicator_dict_id" filterable>
              <el-option v-for="item in indicators" :key="item.id" :label="`${item.name}（${item.unit || '-'}）`" :value="item.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="值"><el-input v-model="indicatorForm.value" /></el-form-item>
          <el-button type="primary" @click="addIndicator">添加</el-button>
        </el-form>
        <el-table :data="current.indicators || []">
          <el-table-column label="指标"><template #default="scope">{{ scope.row.indicator?.name }}</template></el-table-column>
          <el-table-column prop="value" label="值" />
          <el-table-column label="单位"><template #default="scope">{{ scope.row.indicator?.unit }}</template></el-table-column>
          <el-table-column v-if="current.status === 'draft'" width="80">
            <template #default="scope"><el-button link type="danger" @click="removeIndicator(scope.row)">删除</el-button></template>
          </el-table-column>
        </el-table>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { defineComponent, h, onMounted, reactive, ref } from "vue";
import { ElDatePicker, ElForm, ElFormItem, ElInput, ElMessage, ElMessageBox, ElOption, ElSelect } from "element-plus";

import { fetchIndicatorDicts } from "../../api/indicators";
import {
  addOrgReportIndicator, createOrgReport, deleteOrgReportIndicator, fetchOrgPackages,
  fetchOrgReport, fetchOrgReports, lockOrgReport, submitOrgReport, uploadOrgReportOcr,
} from "../../api/org";

const ReportIdentityForm = defineComponent({
  props: { form: Object, packages: Array },
  setup: (props) => () => h(ElForm, { labelPosition: "top" }, () => [
    h(ElFormItem, { label: "受检者真实姓名" }, () => h(ElInput, { modelValue: props.form.subject_name, "onUpdate:modelValue": (value) => { props.form.subject_name = value; } })),
    h(ElFormItem, { label: "健康身份码" }, () => h(ElInput, { modelValue: props.form.subject_health_id, "onUpdate:modelValue": (value) => { props.form.subject_health_id = value; } })),
    h(ElFormItem, { label: "体检日期" }, () => h(ElDatePicker, { modelValue: props.form.exam_date, "onUpdate:modelValue": (value) => { props.form.exam_date = value; }, valueFormat: "YYYY-MM-DD", style: "width:100%" })),
    h(ElFormItem, { label: "套餐（可选）" }, () => h(ElSelect, { modelValue: props.form.package_id, "onUpdate:modelValue": (value) => { props.form.package_id = value; }, clearable: true, style: "width:100%" }, () => [
      h(ElOption, { label: "暂不选择", value: null }),
      ...(props.packages || []).map((item) => h(ElOption, { label: item.name, value: item.id, key: item.id })),
    ])),
  ]),
});

const statuses = [
  { value: "draft", label: "草稿" },
  { value: "locked", label: "已锁定" },
  { value: "published", label: "已归档" },
];
const label = (value) => statuses.find((item) => item.value === value)?.label || value;
const items = ref([]);
const packages = ref([]);
const indicators = ref([]);
const loading = ref(false);
const status = ref("");
const createVisible = ref(false);
const ocrVisible = ref(false);
const ocrLoading = ref(false);
const ocrFile = ref(null);
const detailVisible = ref(false);
const current = ref(null);
const form = reactive({ subject_name: "", subject_health_id: "", exam_date: new Date().toISOString().slice(0, 10), package_id: null });
const indicatorForm = reactive({ indicator_dict_id: null, value: "" });

async function load() {
  loading.value = true;
  try { items.value = (await fetchOrgReports(status.value ? { status: status.value } : {})).data.items || []; }
  finally { loading.value = false; }
}

async function create() {
  try { await createOrgReport(form); createVisible.value = false; ElMessage.success("报告草稿已创建"); await load(); }
  catch (error) { ElMessage.error(error?.response?.data?.message || "创建失败"); }
}

async function runOcr() {
  if (!ocrFile.value) { ElMessage.error("请选择报告文件"); return; }
  ocrLoading.value = true;
  try {
    const { data } = await uploadOrgReportOcr(ocrFile.value, form);
    ocrVisible.value = false;
    ElMessage.success(`OCR 草稿已创建，自动映射 ${data.item.indicator_count} 项，请复核`);
    await load();
    await openDetail(data.item);
  } catch (error) { ElMessage.error(error?.response?.data?.message || "OCR 解析失败"); }
  finally { ocrLoading.value = false; }
}

async function openDetail(report) { current.value = (await fetchOrgReport(report.id)).data.item; detailVisible.value = true; }
async function addIndicator() {
  try { await addOrgReportIndicator(current.value.id, indicatorForm); indicatorForm.value = ""; await openDetail(current.value); }
  catch (error) { ElMessage.error(error?.response?.data?.message || "添加失败"); }
}
async function removeIndicator(indicator) { await deleteOrgReportIndicator(current.value.id, indicator.id); await openDetail(current.value); }
async function lock(report) {
  try { await ElMessageBox.confirm("锁定后报告和指标不可修改，确认继续？", "确认锁定", { type: "warning" }); await lockOrgReport(report.id); await load(); }
  catch (error) { if (error !== "cancel" && error !== "close") ElMessage.error(error?.response?.data?.message || "锁定失败"); }
}
async function submit(report) {
  try {
    await ElMessageBox.confirm("提交后报告将永久归档到对应用户，不可修改或撤下。确认提交？", "提交并归档", { type: "warning", confirmButtonText: "确认提交" });
    await submitOrgReport(report.id); ElMessage.success("报告已自动归档到对应用户"); await load();
  }
  catch (error) { if (error !== "cancel" && error !== "close") ElMessage.error(error?.response?.data?.message || "未找到身份信息完全匹配的注册用户"); }
}

onMounted(async () => {
  const [packageResponse, indicatorResponse] = await Promise.all([fetchOrgPackages(), fetchIndicatorDicts()]);
  packages.value = (packageResponse.data.items || []).filter((item) => item.is_active);
  indicators.value = indicatorResponse.data.items || [];
  indicatorForm.indicator_dict_id = indicators.value[0]?.id;
  await load();
});
</script>
