<template>
  <div class="workspace-page user-platform-page health-data-detail-page">
    <section class="health-detail-hero">
      <el-button class="health-detail-hero__back" text @click="router.back()">← 返回健康资料</el-button>
      <div v-if="item" class="health-detail-hero__content">
        <div>
          <el-tag :type="item.source_type === 'self' ? 'info' : 'success'" effect="light">
            {{ item.source_type === "self" ? "本人记录" : "机构体检" }}
          </el-tag>
          <h2>{{ detailTitle }}</h2>
          <p>{{ formatDate(item.business_date) }} · {{ sourceLabel(item.source_type, item.source) }}</p>
        </div>
        <div class="health-detail-hero__summary">
          <div><strong>{{ indicatorCount }}</strong><span>项指标</span></div>
          <div v-if="item.source_type !== 'self'"><strong>{{ abnormalCount }}</strong><span>项需关注</span></div>
          <el-button v-if="canEditSelf" type="primary" plain @click="openNewMeasurement">记录新测量</el-button>
        </div>
      </div>
    </section>

    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />

    <section v-if="domainOptions.length > 1" class="health-domain-filter" aria-label="筛选健康方向">
      <div>
        <span>筛选健康方向</span>
        <small>已显示 {{ visibleSections.length }}/{{ domainOptions.length }} 个方向</small>
      </div>
      <el-select
        :model-value="selectedDomainIds"
        multiple
        filterable
        collapse-tags
        :max-collapse-tags="2"
        placeholder="选择一个或多个健康方向"
        @change="handleDomainSelection"
      >
        <el-option
          v-for="domain in domainOptions"
          :key="domain.id"
          :label="domain.name"
          :value="domain.id"
        />
      </el-select>
      <el-button plain @click="selectAllDomains">全部方向</el-button>
    </section>

    <div v-loading="loading" class="health-domain-sections">
      <section v-for="section in visibleSections" :key="section.domain.id" class="health-domain-card">
        <header class="health-domain-card__header">
          <div><span>健康方向</span><h3>{{ section.domain.name }}</h3></div>
          <small>{{ section.indicators.length }} 项指标</small>
        </header>

        <div v-if="section.indicators.length" class="health-indicator-list">
          <article v-for="indicator in section.indicators" :key="indicator.id" class="health-indicator-row" :class="{ 'is-abnormal': indicator.is_abnormal }">
            <div class="health-indicator-row__name">
              <strong>{{ indicator.indicator?.name || indicator.original_name || "健康指标" }}</strong>
              <span v-if="item.source_type === 'self'">{{ formatDateTime(indicator.measured_at) }}</span>
              <span v-else-if="indicator.reference_text">参考范围 {{ indicator.reference_text }}</span>
            </div>
            <div class="health-indicator-row__value">
              <strong>{{ indicator.value }}</strong>
              <span>{{ indicator.normalized_unit || indicator.unit || indicator.indicator?.unit || "" }}</span>
            </div>
            <el-tag v-if="item.source_type !== 'self' && shouldDisplayResultStatus(indicator.result_status)" :type="resultStatusMeta(indicator.result_status, indicator.indicator, indicator.value).type" effect="light">
              {{ resultStatusMeta(indicator.result_status, indicator.indicator, indicator.value).label }}
            </el-tag>
            <el-button v-if="canEditSelf" link type="primary" @click="openEditMeasurement(indicator)">修改</el-button>
          </article>
        </div>

        <article v-for="text in section.text_results" :key="text.id" class="health-conclusion-card">
          <span>机构结论</span>
          <h4>{{ text.title }}</h4>
          <p>{{ text.body }}</p>
        </article>

        <div v-if="section.assets.length" class="health-asset-grid">
          <article v-for="asset in section.assets" :key="asset.id">
            <span class="health-asset-grid__icon">{{ asset.modality === "pdf" ? "PDF" : "影" }}</span>
            <div><strong>{{ asset.title }}</strong><p>{{ asset.annotation || "机构检查附件" }}</p></div>
            <el-button link type="primary" @click="openAsset(asset)">查看附件</el-button>
          </article>
        </div>
      </section>

      <div v-if="!loading && item && !item.sections?.length" class="user-empty-state user-empty-state--page">
        <span class="user-empty-state__icon">档</span>
        <div><strong>这份资料暂时没有可展示的内容</strong><p>如有疑问，请联系提供资料的体检机构。</p></div>
      </div>
    </div>

    <MeasurementDrawer
      v-model="measurementVisible"
      :initial-item="selectedMeasurement"
      @saved="reloadAfterMeasurement"
      @deleted="reloadAfterMeasurement"
    />

    <el-dialog
      v-model="assetPreviewVisible"
      class="health-asset-preview-dialog"
      width="min(94vw, 1100px)"
      :title="previewAsset?.title || '检查附件'"
      destroy-on-close
      @closed="closeAssetPreview"
    >
      <div class="health-asset-preview">
        <img
          v-if="previewAsset?.modality !== 'pdf'"
          :src="previewUrl"
          :alt="previewAsset?.title || '检查附件'"
          :class="{ zoomed: assetZoomed }"
          title="点击切换原图缩放"
          @click="assetZoomed = !assetZoomed"
        />
        <iframe v-else :src="previewUrl" title="PDF 检查附件" />
        <p>{{ previewAsset?.annotation || "机构检查附件" }}</p>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { useRoute, useRouter } from "vue-router";
import MeasurementDrawer from "../components/MeasurementDrawer.vue";
import { fetchHealthAssetContent, fetchHealthDataDetail } from "../api/health";
import { formatDate, formatDateTime, resultStatusMeta, shouldDisplayResultStatus, sourceLabel } from "../utils/userPlatform";

const route = useRoute();
const router = useRouter();
const item = ref(null);
const loading = ref(false);
const error = ref("");
const measurementVisible = ref(false);
const selectedMeasurement = ref(null);
const assetPreviewVisible = ref(false);
const previewUrl = ref("");
const previewAsset = ref(null);
const assetZoomed = ref(false);
const selectedDomainIds = ref([]);

const domainOptions = computed(() => (item.value?.sections || []).map((section) => section.domain));
const visibleSections = computed(() => {
  const selected = new Set(selectedDomainIds.value);
  return (item.value?.sections || []).filter((section) => selected.has(section.domain.id));
});
const allIndicators = computed(() => visibleSections.value.flatMap((section) => section.indicators || []));
const indicatorCount = computed(() => allIndicators.value.length);
const abnormalCount = computed(() => allIndicators.value.filter((indicator) => indicator.is_abnormal).length);
const canEditSelf = computed(() => item.value?.source_type === "self" && !route.query.owner_id);
const detailTitle = computed(() => item.value?.source_type === "self"
  ? "当天的日常测量"
  : item.value?.package?.name || "体检健康数据");

function ownerParams() {
  return route.query.owner_id ? { owner_id: route.query.owner_id } : {};
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const nextItem = (await fetchHealthDataDetail(route.params.id, ownerParams())).data.item;
    const availableIds = (nextItem.sections || []).map((section) => section.domain.id);
    const retainedIds = selectedDomainIds.value.filter((id) => availableIds.includes(id));
    item.value = nextItem;
    selectedDomainIds.value = retainedIds.length ? retainedIds : availableIds;
  } catch (fetchError) {
    error.value = fetchError?.response?.data?.message || "这份健康资料暂时无法打开";
  } finally {
    loading.value = false;
  }
}

function handleDomainSelection(ids) {
  if (!ids.length) {
    ElMessage.info("请至少保留一个健康方向");
    return;
  }
  selectedDomainIds.value = ids;
}

function selectAllDomains() {
  selectedDomainIds.value = domainOptions.value.map((domain) => domain.id);
}

function openNewMeasurement() {
  selectedMeasurement.value = null;
  measurementVisible.value = true;
}

function openEditMeasurement(indicator) {
  selectedMeasurement.value = indicator;
  measurementVisible.value = true;
}

async function reloadAfterMeasurement() {
  try {
    await load();
  } catch {
    ElMessage.info("记录已更新，请返回列表查看最新日期");
  }
}

async function openAsset(asset) {
  try {
    const { data } = await fetchHealthAssetContent(route.params.id, asset.id, ownerParams());
    closeAssetPreview();
    previewAsset.value = asset;
    previewUrl.value = URL.createObjectURL(data);
    assetPreviewVisible.value = true;
  } catch (assetError) {
    ElMessage.error(assetError?.response?.data?.message || "附件暂时无法打开");
  }
}

function closeAssetPreview() {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
  previewUrl.value = "";
  previewAsset.value = null;
  assetZoomed.value = false;
}

onMounted(load);
onBeforeUnmount(closeAssetPreview);
</script>

<style scoped>
.health-domain-filter {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) minmax(300px, 1.5fr) auto;
  gap: 14px;
  align-items: center;
  margin-bottom: 18px;
  padding: 16px 18px;
  border: 1px solid var(--color-border);
  border-radius: 14px;
  background: var(--color-surface);
}

.health-domain-filter > div {
  display: grid;
  gap: 4px;
}

.health-domain-filter span {
  font-weight: 700;
}

.health-domain-filter small {
  color: var(--color-text-muted);
}

.health-domain-filter :deep(.el-select) {
  width: 100%;
}

@media (max-width: 680px) {
  .health-domain-filter {
    grid-template-columns: 1fr;
  }

  .health-domain-filter :deep(.el-button) {
    width: 100%;
  }
}

.health-asset-preview {
  display: grid;
  gap: 14px;
  max-height: 74vh;
  overflow: auto;
  padding: 4px;
  background: #111b1d;
  border-radius: 12px;
}

.health-asset-preview img {
  display: block;
  width: 100%;
  height: auto;
  max-height: 66vh;
  object-fit: contain;
  border-radius: 8px;
  background: #071012;
  cursor: zoom-in;
}

.health-asset-preview img.zoomed {
  width: auto;
  max-width: none;
  max-height: none;
  margin: auto;
  cursor: zoom-out;
}

.health-asset-preview iframe {
  width: 100%;
  height: 66vh;
  border: 0;
  border-radius: 8px;
  background: #fff;
}

.health-asset-preview p {
  margin: 0;
  color: #d3e5e2;
  font-size: 12px;
  line-height: 1.6;
}
</style>
