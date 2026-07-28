const CODE_MESSAGES = Object.freeze({
  INVALID_CAPTCHA: "验证码不正确，请重新输入",
  APPOINTMENT_DATE_CONFLICT: "当天已有预约，请先查看或取消原预约后再选择其他日期",
  APPOINTMENT_FULL: "当前日期剩余名额不足，请选择其他日期或开启空位提醒",
  rate_limited: "请求过于频繁，请稍后再试",
  owner_access_denied: "当前没有查看该成员健康数据的授权",
});

const EXACT_MESSAGES = Object.freeze({
  "invalid captcha": "验证码不正确，请重新输入",
  "invalid username or password": "用户名或密码不正确",
  "account is inactive": "该账号已停用，请联系管理员",
  "user not found": "账号不存在或已不可用",
  "institution not found": "未找到该体检分院",
  "package booking notice must be confirmed": "请先阅读并确认检查前须知",
  "booking group not found": "未找到该预约记录",
  "appointment not found": "未找到该预约记录",
  "permission denied": "当前账号没有执行此操作的权限",
  "comment not found": "未找到该评价",
  "asset not found": "未找到该检查附件",
  "asset content unavailable": "检查附件暂时无法读取",
});

export function normalizeApiMessage(message, code, fallback = "操作没有完成，请稍后重试") {
  if (code && CODE_MESSAGES[code]) return CODE_MESSAGES[code];
  const text = String(message || "").trim();
  if (!text) return fallback;
  if (EXACT_MESSAGES[text]) return EXACT_MESSAGES[text];
  // Never expose a raw English-only validation/database message in the UI.
  if (/^[\x00-\x7F]+$/.test(text) && /[A-Za-z]/.test(text)) return fallback;
  return text;
}
