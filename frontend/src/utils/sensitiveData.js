export const HEALTH_ID_REDACTION = "[健康身份码已脱敏]";

const HEALTH_ID_PATTERN =
  /HID-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{8}/giu;

function redactString(value) {
  return value.replace(HEALTH_ID_PATTERN, HEALTH_ID_REDACTION);
}

export function redactHealthIdentityCodes(value) {
  if (typeof value === "string") return redactString(value);
  if (Array.isArray(value)) return value.map(redactHealthIdentityCodes);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        redactString(key),
        redactHealthIdentityCodes(item),
      ])
    );
  }
  return value;
}
