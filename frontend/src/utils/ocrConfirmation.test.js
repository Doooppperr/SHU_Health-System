import { describe, expect, it } from "vitest";

import {
  buildOcrConfirmedMappings,
  createOcrMappingRows,
} from "./ocrConfirmation";

const indicatorDicts = [
  { id: 2, code: "FBG", value_type: "numeric", unit: "mmol/L" },
  { id: 9, code: "UA", value_type: "numeric", unit: "μmol/L" },
  { id: 11, code: "NOTE", value_type: "text", unit: "" },
];

describe("OCR confirmation payload normalization", () => {
  it("normalizes candidate IDs to numbers when creating editable rows", () => {
    const rows = createOcrMappingRows([
      {
        field_index: 3,
        label: "空腹血糖",
        value: "6.8 mmol/L",
        indicator_dict_id: "2",
        indicator_code: "FBG",
        indicator_name: "空腹血糖",
      },
      {
        field_index: 4,
        label: "无效候选",
        value: "1",
        indicator_dict_id: "not-a-number",
      },
    ]);

    expect(rows[0]).toMatchObject({ indicator_dict_id: 2, ignored: false });
    expect(rows[1].indicator_dict_id).toBeNull();
  });

  it("submits numeric IDs while preserving OCR values for strict backend validation", () => {
    const { mappings, invalidRows } = buildOcrConfirmedMappings(
      [
        {
          label: "空腹血糖",
          indicator_dict_id: "2",
          value: "6.8 mmol/L (reference 3.9 - 6.1)",
          score: "0.97",
          ignored: false,
        },
        {
          label: "尿酸",
          indicator_dict_id: 9,
          value: "389 umol/L",
          score: 1,
          ignored: false,
        },
        {
          label: "备注",
          indicator_dict_id: 11,
          value: "建议复查",
          ignored: false,
        },
      ],
      indicatorDicts
    );

    expect(invalidRows).toEqual([]);
    expect(mappings).toEqual([
      {
        indicator_dict_id: 2,
        value: "6.8 mmol/L (reference 3.9 - 6.1)",
        score: 0.97,
      },
      { indicator_dict_id: 9, value: "389 umol/L", score: 1 },
      { indicator_dict_id: 11, value: "建议复查", score: 1 },
    ]);
    expect(mappings.every((item) => Number.isInteger(item.indicator_dict_id))).toBe(true);
  });

  it("excludes ignored rows but blocks cleared, stale, or empty active rows", () => {
    const { mappings, invalidRows } = buildOcrConfirmedMappings(
      [
        { label: "忽略项", indicator_dict_id: 999, value: "bad", ignored: true },
        { label: "清空选项", indicator_dict_id: null, value: "5.2", ignored: false },
        { label: "陈旧选项", indicator_dict_id: 999, value: "5.2", ignored: false },
        { label: "空值", indicator_dict_id: 2, value: "  ", ignored: false },
      ],
      indicatorDicts
    );

    expect(mappings).toEqual([]);
    expect(invalidRows.map((item) => item.label)).toEqual([
      "清空选项",
      "陈旧选项",
      "空值",
    ]);
  });

  it("never guesses which number is the result in ambiguous OCR text", () => {
    const result = buildOcrConfirmedMappings(
      [
        {
          label: "空腹血糖",
          indicator_dict_id: 2,
          value: "5.6 / 6.1",
          ignored: false,
        },
      ],
      indicatorDicts
    );

    expect(result.invalidRows).toEqual([]);
    expect(result.mappings[0].value).toBe("5.6 / 6.1");
  });

  it("keeps zero as a valid numeric result", () => {
    const result = buildOcrConfirmedMappings(
      [{ label: "空腹血糖", indicator_dict_id: 2, value: 0, ignored: false }],
      indicatorDicts
    );

    expect(result.invalidRows).toEqual([]);
    expect(result.mappings[0].value).toBe("0");
  });
});
