export const CORE_PROFILE_FIELDS = ["real_name", "birth_date", "gender"];
export const BUSINESS_TIME_ZONE = "Asia/Shanghai";

export function isBasicProfileComplete(user = {}) {
  if (typeof user.identity_completed === "boolean") return user.identity_completed;
  if (user.identity_completed_at) return true;
  if (typeof user.profile_completed === "boolean") return user.profile_completed;
  if (typeof user.basic_profile_completed === "boolean") return user.basic_profile_completed;
  if (user.basic_profile_completed_at || user.profile_completed_at) return true;
  return CORE_PROFILE_FIELDS.every((field) => String(user[field] || "").trim());
}

export function localDateString(value = new Date()) {
  return [
    value.getFullYear(),
    String(value.getMonth() + 1).padStart(2, "0"),
    String(value.getDate()).padStart(2, "0"),
  ].join("-");
}

export function businessDateString(value = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: BUSINESS_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(value);
  const byType = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${byType.year}-${byType.month}-${byType.day}`;
}

export function shiftCalendarDate(value, days) {
  const matched = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!matched) return "";
  const shifted = new Date(Date.UTC(
    Number(matched[1]),
    Number(matched[2]) - 1,
    Number(matched[3]) + Number(days || 0),
  ));
  return [
    shifted.getUTCFullYear(),
    String(shifted.getUTCMonth() + 1).padStart(2, "0"),
    String(shifted.getUTCDate()).padStart(2, "0"),
  ].join("-");
}

export function bookingDateBounds(now = new Date()) {
  const today = businessDateString(now);
  const minString = shiftCalendarDate(today, 1);
  const maxString = shiftCalendarDate(today, 30);
  return {
    min: new Date(`${minString}T00:00:00`),
    max: new Date(`${maxString}T00:00:00`),
    minString,
    maxString,
  };
}

export function isBookingDateDisabled(value, now = new Date()) {
  const selected = localDateString(new Date(value));
  const { minString, maxString } = bookingDateBounds(now);
  return selected < minString || selected > maxString;
}

export const COMPLAINT_STATUS = {
  institution_pending: { label: "待机构处理", type: "warning" },
  pending_institution: { label: "待机构处理", type: "warning" },
  institution_processing: { label: "机构处理中", type: "warning" },
  user_confirmation: { label: "待你确认", type: "primary" },
  awaiting_user_confirmation: { label: "待你确认", type: "primary" },
  platform_pending: { label: "待平台处理", type: "danger" },
  escalated: { label: "平台处理中", type: "danger" },
  platform_processing: { label: "平台处理中", type: "danger" },
  admin_processing: { label: "平台处理中", type: "danger" },
  resolved: { label: "已解决", type: "success" },
  closed: { label: "已解决", type: "success" },
};

export function complaintMeta(status) {
  return COMPLAINT_STATUS[status] || { label: "处理中", type: "info" };
}

const STATUS_TEXT = {
  high: "偏高",
  low: "偏低",
  positive: "阳性",
  abnormal: "异常",
};

export function collectAbnormalTrendItems(series = [], domain = null) {
  return series
    .flatMap((entry) => {
      const points = [...(entry.points || [])].sort(
        (a, b) => String(a.date || "").localeCompare(String(b.date || ""))
      );
      const latestPoint = points.at(-1);
      const latestRecovered = Boolean(
        latestPoint
        && ["normal", "negative"].includes(latestPoint.result_status)
      );
      const reference = entry.reference || {};
      const bounds = reference.low != null && reference.high != null
        ? `${reference.low}–${reference.high}`
        : reference.low != null
          ? `不低于 ${reference.low}`
          : reference.high != null
            ? `低于 ${reference.high}`
            : "";
      return points
        .filter((point) => point.is_abnormal || STATUS_TEXT[point.result_status])
        .map((point) => ({
          key: `${entry.indicator?.id || entry.indicator?.code}-${point.date}-${point.source?.id || "self"}`,
          indicatorId: entry.indicator?.id || entry.indicator?.code,
          domain: domain?.name || "当前健康方向",
          indicator: entry.indicator?.name || "健康指标",
          unit: entry.indicator?.unit || point.unit || "",
          value: point.value,
          date: point.date,
          direction: STATUS_TEXT[point.result_status] || "异常",
          source: point.source?.type === "self"
            ? "个人日常测量"
            : [point.source?.name, point.source?.branch_name].filter(Boolean).join(" · ") || "机构体检",
          reference: point.reference || reference.label || [
            bounds,
            entry.indicator?.unit || point.unit || "",
          ].filter(Boolean).join(" "),
          detailId: point.health_data_id || point.detail_id || null,
          latestRecovered: latestRecovered && point !== latestPoint,
          latestDate: latestPoint?.date || null,
        }));
    })
    .sort((a, b) => String(b.date).localeCompare(String(a.date)));
}

const NORMAL_STEPS = [
  { key: "booked", label: "预约成功" },
  { key: "attended", label: "已到检" },
  { key: "report_uploaded", label: "报告已上传" },
  { key: "pending_review", label: "待复核" },
  { key: "published", label: "报告已发布" },
];

export function appointmentProgress(appointment = {}) {
  const status = appointment.status || "unfulfilled";
  const events = appointment.events || appointment.progress_events || [];
  const eventTypes = new Set(events.map((event) => event.type || event.event_type));
  const eventTime = (...types) => [...events].reverse().find(
    (event) => types.includes(event.type || event.event_type),
  )?.occurred_at || null;
  const stageIndex = {
    booked: 0,
    attended: 1,
    report_uploaded: 2,
    pending_review: 3,
    published: 4,
  }[appointment.progress_stage];
  let active = 0;
  if (stageIndex != null) active = stageIndex;
  if (status === "awaiting_report" || appointment.attended_at || eventTypes.has("attended")) active = Math.max(active, 1);
  if (appointment.report_id || appointment.report_status === "draft"
      || eventTypes.has("report_uploaded")) active = Math.max(active, 2);
  if (["pending_review", "locked"].includes(appointment.report_status)
      || eventTypes.has("pending_review") || eventTypes.has("submitted_review")) active = Math.max(active, 3);
  if (appointment.report_status === "published" || status === "fulfilled"
      || appointment.fulfilled_at || eventTypes.has("report_published")
      || eventTypes.has("archived")) active = Math.max(active, 4);

  const terminal = {
    cancelled: "用户已取消",
    invalidated: "预约已失效",
    no_show: "未到检",
    institution_cancelled: "机构已取消",
  }[status];
  const stepTimes = {
    booked: eventTime("booked") || appointment.created_at || null,
    attended: eventTime("attended") || appointment.attended_at || null,
    report_uploaded: eventTime("report_uploaded") || null,
    pending_review: eventTime("pending_review", "submitted_review")
      || appointment.submitted_for_review_at
      || null,
    published: eventTime("report_published", "archived")
      || appointment.published_at
      || appointment.fulfilled_at
      || null,
  };
  const steps = NORMAL_STEPS.map((step, index) => ({
    ...step,
    state: index < active ? "done" : index === active ? "current" : "pending",
    occurred_at: stepTimes[step.key],
  }));
  if (terminal) {
    const occurredAt = appointment.cancelled_at || appointment.invalidated_at
      || events.at(-1)?.occurred_at || null;
    steps.splice(active + 1);
    // A terminal branch happens after every retained normal node.  In
    // particular, a cancellation immediately after booking must render
    // “预约成功” as completed instead of leaving two simultaneous current
    // nodes.
    steps.forEach((step) => {
      step.state = "done";
    });
    steps.push({ key: status, label: terminal, state: "terminal", occurred_at: occurredAt });
  }
  return { active, terminal, steps };
}

export function normalizeAppointmentParticipants(group = {}) {
  if (Array.isArray(group.appointments) && group.appointments.length) {
    return group.appointments.map((item) => ({
      ...item,
      id: item.id ?? item.appointment_id,
      user: item.user || { name: item.display_name },
      subject_name_snapshot: item.subject_name_snapshot || item.display_name,
    }));
  }
  return (group.participant_names || []).map((name, index) => ({
    id: `${group.id}-participant-${index}`,
    status: group.status_codes?.[index] || group.status_codes?.[0] || "unfulfilled",
    user: { name },
    appointment_date: group.appointment_date,
  }));
}
