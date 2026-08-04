<template>
  <ol class="appointment-progress" :aria-label="`预约进度：${currentLabel}`">
    <li v-for="step in progress.steps" :key="step.key" :class="`is-${step.state}`">
      <span class="appointment-progress__dot">{{ step.state === "done" ? "✓" : step.state === "terminal" ? "×" : "" }}</span>
      <strong>{{ step.label }}</strong>
      <time v-if="step.occurred_at" :datetime="step.occurred_at" :title="formatProgressTime(step.occurred_at, true)">
        {{ formatProgressTime(step.occurred_at) }}
      </time>
    </li>
  </ol>
</template>

<script setup>
import { computed } from "vue";
import { appointmentProgress } from "../utils/v12";

const props = defineProps({
  appointment: { type: Object, required: true },
});

const progress = computed(() => appointmentProgress(props.appointment));
const currentLabel = computed(() => (
  progress.value.steps.find((step) => ["current", "terminal"].includes(step.state))?.label
  || progress.value.steps.at(-1)?.label
  || "预约成功"
));

function formatProgressTime(value, detailed = false) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  return parsed.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    ...(detailed ? { year: "numeric", second: "2-digit" } : {}),
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}
</script>

<style scoped>
.appointment-progress {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(72px, 1fr));
  margin: 14px 0 4px;
  padding: 0;
  list-style: none;
}

.appointment-progress li {
  position: relative;
  display: grid;
  justify-items: center;
  gap: 6px;
  color: var(--el-text-color-placeholder);
  font-size: 12px;
  text-align: center;
}

.appointment-progress time {
  color: var(--el-text-color-secondary);
  font-size: 10px;
  line-height: 1.2;
}

.appointment-progress li::before {
  position: absolute;
  top: 9px;
  right: 50%;
  left: -50%;
  z-index: 0;
  height: 2px;
  background: var(--el-border-color);
  content: "";
}

.appointment-progress li:first-child::before {
  display: none;
}

.appointment-progress__dot {
  position: relative;
  z-index: 1;
  display: grid;
  width: 20px;
  height: 20px;
  place-items: center;
  border: 2px solid var(--el-border-color);
  border-radius: 50%;
  background: var(--el-bg-color);
  font-size: 11px;
}

.appointment-progress li.is-done,
.appointment-progress li.is-current {
  color: #167363;
}

.appointment-progress li.is-done::before,
.appointment-progress li.is-current::before {
  background: #66b8a8;
}

.appointment-progress li.is-done .appointment-progress__dot,
.appointment-progress li.is-current .appointment-progress__dot {
  border-color: #2b9a86;
  background: #2b9a86;
  color: white;
}

.appointment-progress li.is-current .appointment-progress__dot::after {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: white;
  content: "";
}

.appointment-progress li.is-terminal {
  color: #c45656;
}

.appointment-progress li.is-terminal .appointment-progress__dot {
  border-color: #e76d6d;
  background: #fff1f1;
}

@media (max-width: 620px) {
  .appointment-progress {
    grid-template-columns: 1fr;
    gap: 8px;
  }

  .appointment-progress li {
    grid-template-columns: 24px minmax(0, 1fr) auto;
    justify-items: start;
    align-items: center;
    text-align: left;
  }

  .appointment-progress li::before {
    top: -12px;
    bottom: 50%;
    left: 9px;
    width: 2px;
    height: auto;
  }
}
</style>
