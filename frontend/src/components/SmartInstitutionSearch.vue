<template>
  <div
    class="smart-institution-search"
    role="combobox"
    :aria-expanded="String(dropdownVisible)"
    aria-haspopup="listbox"
    @focusin="open = true"
    @focusout="handleFocusOut"
  >
    <el-input
      :model-value="modelValue"
      clearable
      :placeholder="placeholder"
      :aria-label="ariaLabel"
      :aria-expanded="String(dropdownVisible)"
      aria-autocomplete="list"
      aria-controls="institution-smart-search-listbox"
      :aria-activedescendant="activeIndex >= 0 ? `institution-suggestion-${activeIndex}` : undefined"
      @update:model-value="handleInput"
      @focus="open = true"
      @keydown="handleKeydown"
    >
      <template #prefix><span class="smart-search-icon" aria-hidden="true">⌕</span></template>
    </el-input>

    <div
      v-if="dropdownVisible"
      id="institution-smart-search-listbox"
      class="smart-search-dropdown"
      role="listbox"
      aria-label="智能搜索推荐"
      @mousedown.prevent
    >
      <div v-if="loading && !suggestions.length" class="smart-search-loading">正在搜索…</div>
      <button
        v-for="(item, index) in suggestions"
        :id="`institution-suggestion-${index}`"
        :key="`${item.kind}-${item.organization_id}-${item.institution_id || 0}-${item.package_id || 0}`"
        type="button"
        role="option"
        class="smart-search-suggestion"
        :class="{ 'is-active': index === activeIndex }"
        :aria-selected="String(index === activeIndex)"
        @mouseenter="activeIndex = index"
        @click="choose(item)"
      >
        <span class="suggestion-kind">{{ kindLabel(item.kind) }}</span>
        <span class="suggestion-copy">
          <strong>
            <template v-for="(part, partIndex) in highlighted(item.title)" :key="partIndex">
              <mark v-if="part.hit">{{ part.text }}</mark><template v-else>{{ part.text }}</template>
            </template>
          </strong>
          <small>{{ item.subtitle }}</small>
          <em>{{ item.reason || "与当前需求相关" }}</em>
        </span>
        <b aria-hidden="true">↵</b>
      </button>
      <div v-if="!loading && !suggestions.length" class="smart-search-empty">
        暂无匹配推荐，可换一个人群、健康方向或地区试试
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  modelValue: { type: String, default: "" },
  placeholder: { type: String, default: "输入机构、分院、套餐、人群、健康方向或交通信息" },
  ariaLabel: { type: String, default: "智能搜索体检机构" },
  search: {
    type: Object,
    default: () => ({ mode: "content", suggestions: [], intent_summary: "" }),
  },
  loading: { type: Boolean, default: false },
});
const emit = defineEmits(["update:modelValue", "search", "select"]);
const open = ref(false);
const activeIndex = ref(-1);
const suggestions = computed(() => props.search?.suggestions || []);
const dropdownVisible = computed(() => open.value && Boolean(props.modelValue.trim()));

watch(suggestions, () => {
  activeIndex.value = suggestions.value.length ? 0 : -1;
}, { immediate: true });

function handleInput(value) {
  emit("update:modelValue", value || "");
  emit("search", value || "");
  open.value = true;
}

function handleFocusOut(event) {
  if (!event.currentTarget.contains(event.relatedTarget)) open.value = false;
}

function choose(item) {
  emit("select", item);
  open.value = false;
}

function handleKeydown(event) {
  if (event.key === "Escape") {
    open.value = false;
    return;
  }
  if (!suggestions.value.length) return;
  if (event.key === "ArrowDown") {
    event.preventDefault();
    open.value = true;
    activeIndex.value = (activeIndex.value + 1) % suggestions.value.length;
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    open.value = true;
    activeIndex.value = (activeIndex.value - 1 + suggestions.value.length) % suggestions.value.length;
  } else if (event.key === "Enter" && open.value && activeIndex.value >= 0) {
    event.preventDefault();
    choose(suggestions.value[activeIndex.value]);
  }
}

function kindLabel(kind) {
  return { organization: "机构", branch: "分院", package: "套餐" }[kind] || "推荐";
}

function highlighted(value) {
  const text = String(value || "");
  const needle = props.modelValue.trim();
  if (!needle) return [{ text, hit: false }];
  const index = text.toLocaleLowerCase().indexOf(needle.toLocaleLowerCase());
  if (index < 0) return [{ text, hit: false }];
  return [
    { text: text.slice(0, index), hit: false },
    { text: text.slice(index, index + needle.length), hit: true },
    { text: text.slice(index + needle.length), hit: false },
  ].filter((part) => part.text);
}
</script>

<style scoped>
.smart-institution-search{position:relative;width:100%}.smart-search-icon{color:var(--workspace-accent);font-size:20px;font-weight:800}.smart-search-dropdown{position:absolute;z-index:80;top:calc(100% + 8px);left:0;right:0;overflow:hidden;border:1px solid var(--color-border);border-radius:14px;background:var(--color-surface);box-shadow:0 18px 50px rgba(16,41,37,.18)}.smart-search-suggestion{display:flex;align-items:center;gap:12px;width:100%;padding:11px 14px;border:0;border-top:1px solid color-mix(in srgb,var(--color-border) 72%,transparent);background:transparent;color:inherit;text-align:left;cursor:pointer}.smart-search-suggestion:first-of-type{border-top:0}.smart-search-suggestion:hover,.smart-search-suggestion.is-active{background:color-mix(in srgb,var(--color-soft) 82%,transparent)}.suggestion-kind{flex:none;padding:4px 7px;border-radius:7px;background:var(--workspace-accent);color:#fff;font-size:11px;font-weight:800}.suggestion-copy{display:grid;min-width:0;flex:1;gap:2px}.suggestion-copy strong,.suggestion-copy small,.suggestion-copy em{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.suggestion-copy strong{font-size:14px}.suggestion-copy small{color:var(--color-muted);font-size:12px}.suggestion-copy em{color:var(--workspace-accent);font-size:12px;font-style:normal}.suggestion-copy mark{padding:0;background:transparent;color:var(--workspace-accent);font-weight:900}.smart-search-suggestion>b{color:var(--color-muted)}.smart-search-loading,.smart-search-empty{padding:24px 14px;color:var(--color-muted);text-align:center;font-size:13px}@media(max-width:640px){.smart-search-dropdown{max-height:min(60vh,480px);overflow:auto}.suggestion-copy em{white-space:normal}}
</style>
