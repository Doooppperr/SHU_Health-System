<template>
  <figure class="institution-cover" :class="`institution-cover--${variant}`">
    <img
      v-if="imageUrl && !loadFailed"
      :src="imageUrl"
      :alt="`${institutionLabel}封面`"
      loading="lazy"
      decoding="async"
      @error="loadFailed = true"
    />
    <div v-else class="institution-cover-placeholder" role="img" :aria-label="`${institutionLabel}暂无封面`">
      <span aria-hidden="true">院</span>
      <small>{{ loadFailed ? "封面暂时无法加载" : "暂无封面" }}</small>
    </div>
  </figure>
</template>

<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  institution: {
    type: Object,
    required: true,
  },
  variant: {
    type: String,
    default: "card",
    validator: (value) => ["card", "detail"].includes(value),
  },
});

const imageUrl = computed(() => (
  props.institution.cover_image_url
  || props.institution.logo_url
  || props.institution.images?.[0]?.image_url
  || ""
));
const institutionLabel = computed(() => (
  [props.institution.name, props.institution.branch_name].filter(Boolean).join("·") || "体检机构"
));
const loadFailed = ref(false);

watch(imageUrl, () => {
  loadFailed.value = false;
});
</script>
