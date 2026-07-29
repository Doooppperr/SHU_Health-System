const TOOL_PROGRESS_LABELS = {
  list_reports: "正在查找可用体检档案",
  get_report_facts: "正在核对报告指标",
  compute_indicator_trend: "正在计算历史趋势",
  search_institutions: "正在查找体检机构",
  compare_packages: "正在核对体检套餐",
  check_availability: "正在检查预约名额",
  get_appointment_status: "正在查询预约状态",
  create_booking_draft: "正在准备预约确认信息",
  create_cancellation_draft: "正在准备取消确认信息",
  create_waitlist_draft: "正在准备空位提醒",
  create_support_handoff_draft: "正在准备人工客服确认信息",
};

export function agentToolProgressLabel(toolName) {
  return TOOL_PROGRESS_LABELS[toolName] || "正在核对相关信息";
}

export function replacePendingAgentAction(actions, nextAction) {
  return [
    ...(actions || []).filter(
      (item) => item.action_id !== nextAction.action_id
        && item.action_type !== nextAction.action_type
    ),
    nextAction,
  ];
}
