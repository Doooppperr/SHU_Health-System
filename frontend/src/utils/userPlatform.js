export const APPOINTMENT_STATUS = {
  unfulfilled: { label: "已预约", type: "primary", hint: "等待到店体检" },
  awaiting_report: { label: "待出结果", type: "warning", hint: "机构正在整理本次健康数据" },
  fulfilled: { label: "已完成", type: "success", hint: "本次健康数据已归档" },
  invalidated: { label: "已失效", type: "danger", hint: "本次预约未能按时完成" },
  no_show: { label: "未到检", type: "danger", hint: "受检者未按预约到检" },
  institution_cancelled: { label: "机构取消", type: "danger", hint: "机构因自身原因取消，请查看解决方案" },
  cancelled: { label: "已取消", type: "info", hint: "本次预约已取消" },
};

export function resultStatusMeta(status, indicator = {}, value = null) {
  if (indicator.code === "BMI" && status === "high") {
    const numeric = Number(value);
    return numeric >= 28
      ? { label: "肥胖", type: "danger" }
      : { label: "超重", type: "warning" };
  }
  return ({
    normal: { label: "正常", type: "success" },
    high: { label: "偏高", type: "danger" },
    low: { label: "偏低", type: "warning" },
    positive: { label: "阳性", type: "danger" },
    negative: { label: "阴性", type: "success" },
    abnormal: { label: "异常", type: "danger" },
  })[status] || { label: "", type: "info" };
}

export function shouldDisplayResultStatus(status) {
  return ["normal", "high", "low", "positive", "negative", "abnormal"].includes(status);
}

export const GENDER_LABELS = {
  all: "不限人群",
  male: "男性",
  female: "女性",
  female_all: "女性",
};

export const WAITLIST_STATUS = {
  active: "提醒中",
  closed: "已有空位",
  cancelled: "已取消",
  invalid: "已失效",
};

export function appointmentMeta(status) {
  return APPOINTMENT_STATUS[status] || { label: "状态更新中", type: "info", hint: "请稍后查看" };
}

export function genderLabel(value) {
  return GENDER_LABELS[value] || "不限人群";
}

export function packageTypeLabel(value) {
  return value === "combined" ? "综合组合" : "专项检查";
}

export function sourceLabel(sourceType, source) {
  if (sourceType === "self") return "本人记录";
  return [source?.name, source?.branch_name].filter(Boolean).join(" · ") || "体检机构";
}

export function normalizeTimelineEntry(entry) {
  const kind = entry.kind || entry.record_type || (entry.type === "appointment" ? "exam" : entry.type);
  if (kind === "self" || entry.source_type === "self") {
    return {
      key: `self-${entry.health_data_id || entry.detail_id || entry.business_date}`,
      kind: "self",
      businessDate: entry.business_date,
      detailId: entry.health_data_id || entry.detail_id,
      title: entry.title || "今日健康记录",
      source: entry.source,
      domains: entry.domains || [],
      indicatorCount: entry.indicator_count ?? entry.summary?.indicator_count ?? 0,
      summary: entry.summary,
    };
  }
  const item = entry.item || entry;
  return {
    key: `exam-${item.id || entry.business_date}`,
    kind: "exam",
    businessDate: entry.business_date || item.appointment_date,
    detailId: entry.health_data_id || item.health_data_id,
    title: entry.title,
    item,
    events: entry.events || [],
  };
}

export function formatDate(value, options = {}) {
  if (!value) return "—";
  const date = /^\d{4}-\d{2}-\d{2}$/.test(String(value))
    ? new Date(`${value}T00:00:00`)
    : new Date(value);
  if (Number.isNaN(date.getTime())) return "日期待核对";
  return date.toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    ...options,
  });
}

export function formatBusinessDate(value, { short = false } = {}) {
  if (!value) return "日期待核对";
  const text = String(value).trim();
  const matched = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!matched) return "日期待核对";
  const date = new Date(Number(matched[1]), Number(matched[2]) - 1, Number(matched[3]));
  if (date.getFullYear() !== Number(matched[1]) || date.getMonth() !== Number(matched[2]) - 1 || date.getDate() !== Number(matched[3])) return "日期待核对";
  return date.toLocaleDateString("zh-CN", short
    ? { month: "short", day: "numeric" }
    : { year: "numeric", month: "long", day: "numeric" });
}

export function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间待核对";
  return date.toLocaleString("zh-CN", { hour12: false });
}
