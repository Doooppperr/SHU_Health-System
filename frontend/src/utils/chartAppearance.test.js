import { describe, expect, it } from "vitest";

import { buildTrendChartOption, buildTrendReferenceArea, resolveTrendAxisBounds, resolveTrendChartAppearance } from "./chartAppearance";

describe("trend chart appearance", () => {
  it("uses the light user palette by default", () => {
    const appearance = resolveTrendChartAppearance();
    const option = buildTrendChartOption({ xAxisData: ["2026-07-12"], yAxisData: [72] });

    expect(appearance).toMatchObject({
      theme: "light",
      accent: "#0b7a6b",
      fontSize: 14,
      axisFontSize: 13,
    });
    expect(option.series[0].lineStyle.color).toBe("#0b7a6b");
    expect(option.tooltip.backgroundColor).toBe("#ffffff");
    expect(option.xAxis.data).toEqual(["2026-07-12"]);
    expect(option.yAxis.splitLine.lineStyle.color).toBe("rgba(210, 210, 215, 0.72)");
    expect(option.yAxis.scale).toBe(true);
    expect(option.series[0].label).toEqual({ show: false });
  });

  it("uses the dark institution palette throughout the chart", () => {
    const option = buildTrendChartOption({ theme: "dark", accent: "institution" });

    expect(option.textStyle.color).toBe("#f5f5f7");
    expect(option.tooltip).toMatchObject({
      backgroundColor: "#2c2c2e",
      borderColor: "#3a3a3c",
    });
    expect(option.xAxis.axisLabel.color).toBe("#b8b8bd");
    expect(option.series[0].lineStyle.color).toBe("#d9a066");
    expect(option.series[0].areaStyle).toBeUndefined();
    expect(option.__appearance.referenceArea).toBe("rgba(240, 179, 95, 0.16)");
  });

  it("increases chart text and controls for care mode", () => {
    const regular = buildTrendChartOption();
    const care = buildTrendChartOption({ careMode: true });

    expect(care.textStyle.fontSize).toBe(17);
    expect(care.xAxis.axisLabel.fontSize).toBe(16);
    expect(care.tooltip.padding).toEqual([12, 14]);
    expect(care.series[0].symbolSize).toBeGreaterThan(regular.series[0].symbolSize);
    expect(care.series[0].lineStyle.width).toBeGreaterThan(regular.series[0].lineStyle.width);
  });

  it("styles reference lines without mutating the source data", () => {
    const referenceLines = [{ yAxis: 60, name: "参考下限" }];
    const option = buildTrendChartOption({ theme: "dark", referenceLines });

    expect(option.series[0].markLine.data[0]).toMatchObject({
      yAxis: 60,
      lineStyle: { color: "#f0b35f", type: "dashed" },
      label: { show: false, color: "#f0b35f" },
    });
    expect(referenceLines).toEqual([{ yAxis: 60, name: "参考下限" }]);
  });

  it("keeps reference text outside the plot and preserves the reference palette", () => {
    const appearance = resolveTrendChartAppearance({ theme: "dark" });
    const area = buildTrendReferenceArea({ low: 57.3, high: 74, label: "成人 BMI 参考体重" }, appearance);

    expect(area).toMatchObject({
      silent: true,
      label: { show: false },
      itemStyle: { color: "rgba(240, 179, 95, 0.16)" },
      data: [[{ yAxis: 57.3 }, { yAxis: 74 }]],
    });
    expect(JSON.stringify(area)).not.toContain("成人 BMI 参考体重");
  });

  it("does not build a shaded band from an incomplete range", () => {
    expect(buildTrendReferenceArea({ low: 60 })).toBeUndefined();
  });

  it("keeps both measurements and the full reference range inside the y axis", () => {
    const bounds = resolveTrendAxisBounds({
      yAxisData: [{ value: 65.1 }, { value: 73.8 }, { value: 71.9 }],
      referenceLines: [{ yAxis: 57.3 }, { yAxis: 74 }],
    });
    const option = buildTrendChartOption({
      yAxisData: [{ value: 65.1 }, { value: 73.8 }],
      referenceLines: [{ yAxis: 57.3 }, { yAxis: 74 }],
    });

    expect(bounds).toEqual({ min: 55, max: 76 });
    expect(option.yAxis).toMatchObject({ min: 55, max: 76, scale: true });
  });

  it("gives a single measurement a readable non-zero axis span", () => {
    expect(resolveTrendAxisBounds({ yAxisData: [72] })).toEqual({ min: 68.4, max: 75.6 });
  });

  it("falls back safely for unsupported theme and accent values", () => {
    expect(resolveTrendChartAppearance({ theme: "sepia", accent: "admin" })).toMatchObject({
      theme: "light",
      accent: "#0b7a6b",
    });
  });
});
