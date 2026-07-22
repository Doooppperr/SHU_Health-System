<template>
  <el-drawer
    :model-value="modelValue"
    :title="form.id ? '修改测量记录' : '记录今日测量'"
    size="min(540px, 100vw)"
    class="measurement-drawer"
    @update:model-value="$emit('update:modelValue', $event)"
    @open="load"
    @closed="resetForm"
  >
    <div class="measurement-drawer__intro">
      <span class="user-kicker">日常记录</span>
      <h2>{{ form.id ? "修正这次记录" : "花一分钟，记下此刻的身体状态" }}</h2>
      <p>选择指标并填写测量值。系统会按日期自动归入健康记录和趋势。</p>
    </div>

    <el-alert v-if="errorMessage" :title="errorMessage" type="error" :closable="false" show-icon />

    <el-form label-position="top" class="measurement-form" @submit.prevent="save">
      <el-form-item label="测量项目" required>
        <el-select v-model="form.indicator_dict_id" filterable placeholder="请选择测量项目" style="width: 100%">
          <el-option
            v-for="indicator in allowed"
            :key="indicator.id"
            :label="`${indicator.name}${indicator.unit ? `（${indicator.unit}）` : ''}`"
            :value="indicator.id"
          />
        </el-select>
      </el-form-item>
      <div class="measurement-form__grid">
        <el-form-item label="测量值" required>
          <el-input-number v-model="form.value" :min="0" :precision="2" :step="0.01" controls-position="right" style="width: 100%" />
        </el-form-item>
        <el-form-item label="测量时间" required>
          <el-date-picker
            v-model="form.measured_at"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ssZ"
            style="width: 100%"
          />
        </el-form-item>
      </div>
      <div class="measurement-form__actions">
        <el-button v-if="form.id" @click="resetForm">改为新增</el-button>
        <el-button type="primary" native-type="submit" :loading="saving">{{ form.id ? "保存修改" : "保存记录" }}</el-button>
      </div>
    </el-form>

    <section class="measurement-recent" aria-labelledby="measurement-recent-title">
      <div class="user-section-heading">
        <div>
          <span>最近记录</span>
          <h3 id="measurement-recent-title">继续保持自己的节奏</h3>
        </div>
        <small>显示最近 7 条</small>
      </div>
      <div v-loading="loading" class="measurement-recent__list">
        <article v-for="item in recentItems" :key="item.id" class="measurement-recent__item">
          <div>
            <strong>{{ item.indicator?.name || "健康指标" }}</strong>
            <span>{{ formatDateTime(item.measured_at) }}</span>
          </div>
          <b>{{ compactValue(item.value) }} <small>{{ item.unit }}</small></b>
          <div class="measurement-recent__actions">
            <el-button link type="primary" @click="edit(item)">修改</el-button>
            <el-button link type="danger" @click="remove(item)">删除</el-button>
          </div>
        </article>
        <el-empty v-if="!loading && !recentItems.length" description="还没有测量记录，今天就从第一条开始" :image-size="80" />
      </div>
    </section>
  </el-drawer>
</template>

<script setup>
import { computed, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { fetchIndicatorDicts } from "../api/indicators";
import {
  createMeasurement,
  deleteMeasurement,
  fetchMeasurements,
  updateMeasurement,
} from "../api/health";
import { formatDateTime } from "../utils/userPlatform";

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  initialItem: { type: Object, default: null },
});
const emit = defineEmits(["update:modelValue", "saved", "deleted"]);

const allowed = ref([]);
const items = ref([]);
const loading = ref(false);
const saving = ref(false);
const errorMessage = ref("");
const form = reactive({ id: null, indicator_dict_id: null, value: null, measured_at: null });
const recentItems = computed(() => items.value.slice(0, 7));

function nowValue() {
  return new Date().toISOString();
}

function compactValue(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return value;
  return Number.isInteger(number) ? number : Number(number.toFixed(2));
}

function resetForm() {
  Object.assign(form, {
    id: null,
    indicator_dict_id: allowed.value[0]?.id || null,
    value: null,
    measured_at: nowValue(),
  });
}

function edit(item) {
  Object.assign(form, {
    id: item.id,
    indicator_dict_id: item.indicator_dict_id,
    value: Number(item.value),
    measured_at: item.measured_at,
  });
}

async function load() {
  loading.value = true;
  errorMessage.value = "";
  try {
    const [measurementResponse, indicatorResponse] = await Promise.all([
      fetchMeasurements({ limit: 7 }),
      fetchIndicatorDicts(),
    ]);
    items.value = measurementResponse.data.items || [];
    allowed.value = (indicatorResponse.data.items || []).filter((item) => item.allow_self_measurement);
    if (props.initialItem) edit(props.initialItem);
    else if (!form.indicator_dict_id) resetForm();
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "测量项目暂时没有加载成功，请稍后重试";
  } finally {
    loading.value = false;
  }
}

async function save() {
  if (!form.indicator_dict_id || form.value === null || !form.measured_at) {
    ElMessage.warning("请完整填写测量项目、测量值和时间");
    return;
  }
  saving.value = true;
  try {
    const payload = {
      indicator_dict_id: form.indicator_dict_id,
      value: Number(form.value),
      measured_at: form.measured_at,
    };
    if (form.id) await updateMeasurement(form.id, payload);
    else await createMeasurement(payload);
    ElMessage.success(form.id ? "测量记录已更新" : "今天的测量已记录");
    emit("saved");
    resetForm();
    await load();
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "保存失败，请检查后重试");
  } finally {
    saving.value = false;
  }
}

async function remove(item) {
  try {
    await ElMessageBox.confirm(
      `确认删除 ${item.indicator?.name || "这条"} 测量记录？`,
      "删除测量记录",
      { type: "warning", confirmButtonText: "确认删除", cancelButtonText: "保留记录" }
    );
    await deleteMeasurement(item.id);
    ElMessage.success("记录已删除");
    emit("deleted");
    if (form.id === item.id) resetForm();
    await load();
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(error?.response?.data?.message || "删除失败");
    }
  }
}

watch(() => props.initialItem, (item) => {
  if (props.modelValue && item) edit(item);
});
</script>
