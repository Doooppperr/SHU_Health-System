<template>
  <div class="workspace-page">
    <section class="page-intro">
      <div><p>脱敏 · 只读</p><h2>机构健康档案</h2><span>仅展示来源于本机构且已确认的标准化数据。</span></div>
    </section>
    <el-alert title="数据边界" description="页面不提供联系方式、上传人资料或原始报告，也不支持新增、编辑和删除健康档案。" type="warning" show-icon :closable="false" />
    <el-alert v-if="errorMessage" :title="errorMessage" type="error" show-icon :closable="false" />

    <el-card shadow="never" class="filter-card">
      <div class="filter-row">
        <label class="filter-field"><span class="filter-field-label">档案归属人</span><el-input v-model="filters.owner_keyword" clearable placeholder="输入显示名" @keyup.enter="search" /></label>
        <el-button type="primary" @click="search">查询</el-button>
        <el-button @click="reset">重置</el-button>
      </div>
    </el-card>

    <el-card shadow="never" class="table-card">
      <el-table :data="items" v-loading="loading" empty-text="暂无符合条件的已确认档案">
        <el-table-column label="档案 ID" width="120"><template #default="scope">{{ formatRecordDisplayId(scope.row) }}</template></el-table-column>
        <el-table-column prop="exam_date" label="体检日期" width="125" />
        <el-table-column label="归属人" min-width="140"><template #default="scope">{{ ownerName(scope.row) }}</template></el-table-column>
        <el-table-column label="套餐" min-width="160"><template #default="scope">{{ scope.row.package?.name || "未选择套餐" }}</template></el-table-column>
        <el-table-column prop="indicator_count" label="指标数" width="90" />
        <el-table-column label="状态" width="100"><template #default><el-tag type="success">已确认</el-tag></template></el-table-column>
        <el-table-column label="操作" width="100" fixed="right"><template #default="scope"><el-button link type="primary" @click="openDetail(scope.row.id)">查看指标</el-button></template></el-table-column>
      </el-table>
      <div class="pagination-row" v-if="pagination.total > pagination.page_size">
        <el-pagination background layout="prev, pager, next, total" :current-page="pagination.page" :page-size="pagination.page_size" :total="pagination.total" @current-change="changePage" />
      </div>
    </el-card>

    <el-drawer v-model="drawerVisible" title="只读档案详情" size="min(720px, 92vw)">
      <el-skeleton v-if="detailLoading" :rows="7" animated />
      <template v-else-if="detail">
        <el-descriptions :column="2" border class="detail-descriptions">
          <el-descriptions-item label="档案 ID">{{ formatRecordDisplayId(detail) }}</el-descriptions-item>
          <el-descriptions-item label="归属人">{{ ownerName(detail) }}</el-descriptions-item>
          <el-descriptions-item label="体检日期">{{ detail.exam_date }}</el-descriptions-item>
          <el-descriptions-item label="套餐">{{ detail.package?.name || "未选择套餐" }}</el-descriptions-item>
        </el-descriptions>
        <h3 class="section-title">标准化指标</h3>
        <el-table :data="detail.indicators || []" empty-text="暂无指标">
          <el-table-column label="指标" min-width="190"><template #default="scope">{{ scope.row.indicator?.code }} · {{ scope.row.indicator?.name }}</template></el-table-column>
          <el-table-column prop="value" label="结果" min-width="110" />
          <el-table-column label="参考范围" min-width="140"><template #default="scope">{{ reference(scope.row.indicator) }}</template></el-table-column>
          <el-table-column label="异常" width="90"><template #default="scope"><el-tag :type="scope.row.is_abnormal ? 'danger' : 'success'">{{ scope.row.is_abnormal ? "异常" : "正常" }}</el-tag></template></el-table-column>
        </el-table>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { fetchOrgHealthRecord, fetchOrgHealthRecords } from "../../api/org";
import { formatRecordDisplayId } from "../../utils/recordDisplayId";

const items = ref([]); const loading = ref(false); const errorMessage = ref(""); const drawerVisible = ref(false); const detailLoading = ref(false); const detail = ref(null);
const filters = reactive({ owner_keyword: "" });
const pagination = reactive({ page: 1, page_size: 20, total: 0, pages: 0 });
const ownerName = (item) => item.owner_display_name || item.owner?.display_name || item.owner?.username || `用户 ${item.owner_id}`;
const reference = (indicator) => {
  if (!indicator) return "-";
  const low = indicator.reference_low; const high = indicator.reference_high; const unit = indicator.unit || "";
  if (low == null && high == null) return unit || "-";
  return `${low ?? "-∞"} ~ ${high ?? "+∞"} ${unit}`.trim();
};
async function load() {
  loading.value = true; errorMessage.value = "";
  try { const { data } = await fetchOrgHealthRecords({ page: pagination.page, page_size: pagination.page_size, owner_keyword: filters.owner_keyword.trim() || undefined }); items.value = data.items || []; Object.assign(pagination, data.pagination || { total: items.value.length }); }
  catch (error) { errorMessage.value = error?.response?.data?.message || "机构健康档案加载失败"; }
  finally { loading.value = false; }
}
function search() { pagination.page = 1; load(); }
function reset() { filters.owner_keyword = ""; search(); }
function changePage(page) { pagination.page = page; load(); }
async function openDetail(id) { drawerVisible.value = true; detailLoading.value = true; detail.value = null; try { const { data } = await fetchOrgHealthRecord(id); detail.value = data.item; } catch (error) { errorMessage.value = error?.response?.data?.message || "档案详情加载失败"; drawerVisible.value = false; } finally { detailLoading.value = false; } }
onMounted(load);
</script>
