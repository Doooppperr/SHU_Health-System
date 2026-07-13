function normalizeIndicatorId(value) {
  const normalized = Number(value);
  return Number.isInteger(normalized) && normalized > 0 ? normalized : null;
}

export function createOcrMappingRows(candidateMappings = []) {
  return candidateMappings.map((item, index) => ({
    row_id: `${item.field_index ?? index}-${item.indicator_dict_id ?? "unmatched"}`,
    label: item.label,
    value: item.value,
    suggested_code: item.indicator_code,
    suggested_name: item.indicator_name,
    indicator_dict_id: normalizeIndicatorId(item.indicator_dict_id),
    score: item.score,
    reason: item.reason,
    ignored: false,
  }));
}

export function buildOcrConfirmedMappings(rows = [], indicatorDicts = []) {
  const dictById = new Map(
    indicatorDicts
      .map((item) => [normalizeIndicatorId(item.id), item])
      .filter(([id]) => id !== null)
  );
  const mappings = [];
  const invalidRows = [];

  rows.forEach((row, index) => {
    if (row.ignored) return;

    const indicatorId = normalizeIndicatorId(row.indicator_dict_id);
    const indicator = indicatorId === null ? null : dictById.get(indicatorId);
    const rawValue = String(row.value ?? "").trim();
    let reason = "";

    if (!indicator) {
      reason = "请选择当前指标字典中的有效指标";
    } else if (!rawValue) {
      reason = "OCR 值不能为空";
    }

    if (reason) {
      invalidRows.push({ index, label: row.label || `第 ${index + 1} 行`, reason });
      return;
    }

    const rawScore = Number(row.score);
    mappings.push({
      indicator_dict_id: indicatorId,
      value: rawValue,
      score: Number.isFinite(rawScore) ? rawScore : 1,
    });
  });

  return { mappings, invalidRows };
}
