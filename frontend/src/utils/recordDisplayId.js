const HEALTH_ID_PATTERN = /^health(\d+)$/i;
const NUMERIC_ID_PATTERN = /^\d+$/;

function normalizeDisplayId(value) {
  const match = String(value ?? "").trim().match(HEALTH_ID_PATTERN);
  return match ? `health${match[1]}` : "";
}

/**
 * Formats a health record's public identifier without changing its numeric API id.
 * API responses may expose either display_id (record resources) or
 * record_display_id (trend series), so both are accepted before falling back to
 * the numeric id.
 */
export function formatRecordDisplayId(recordOrId) {
  if (recordOrId && typeof recordOrId === "object") {
    const displayId =
      normalizeDisplayId(recordOrId.display_id) ||
      normalizeDisplayId(recordOrId.record_display_id);

    if (displayId) return displayId;

    return formatRecordDisplayId(recordOrId.id ?? recordOrId.record_id);
  }

  const numericId = String(recordOrId ?? "").trim();
  return NUMERIC_ID_PATTERN.test(numericId) ? `health${numericId}` : "-";
}
