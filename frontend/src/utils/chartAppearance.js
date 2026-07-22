const FONT_FAMILY =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif';

const THEME_PALETTES = {
  light: {
    text: "#1d1d1f",
    secondaryText: "#5f6368",
    axis: "#86868b",
    grid: "rgba(210, 210, 215, 0.72)",
    tooltipBackground: "#ffffff",
    tooltipBorder: "#d2d2d7",
    tooltipShadow: "0 10px 30px rgba(0, 0, 0, 0.12)",
    referenceLine: "#a55b00",
    referenceArea: "rgba(230, 159, 0, 0.13)",
  },
  dark: {
    text: "#f5f5f7",
    secondaryText: "#b8b8bd",
    axis: "#8e8e93",
    grid: "rgba(184, 184, 189, 0.22)",
    tooltipBackground: "#2c2c2e",
    tooltipBorder: "#3a3a3c",
    tooltipShadow: "0 10px 30px rgba(0, 0, 0, 0.36)",
    referenceLine: "#f0b35f",
    referenceArea: "rgba(240, 179, 95, 0.16)",
  },
};

const ACCENT_PALETTES = {
  user: {
    light: "#0b7a6b",
    dark: "#63d4c1",
    lightArea: "rgba(11, 122, 107, 0.12)",
    darkArea: "rgba(99, 212, 193, 0.16)",
  },
  institution: {
    light: "#9a642e",
    dark: "#d9a066",
    lightArea: "rgba(154, 100, 46, 0.12)",
    darkArea: "rgba(217, 160, 102, 0.16)",
  },
};

export function resolveTrendChartAppearance({
  theme = "light",
  careMode = false,
  accent = "user",
} = {}) {
  const resolvedTheme = theme === "dark" ? "dark" : "light";
  const resolvedAccent = accent === "institution" ? "institution" : "user";
  const palette = THEME_PALETTES[resolvedTheme];
  const accentPalette = ACCENT_PALETTES[resolvedAccent];

  return {
    ...palette,
    theme: resolvedTheme,
    accent: accentPalette[resolvedTheme],
    area: accentPalette[`${resolvedTheme}Area`],
    fontFamily: FONT_FAMILY,
    fontSize: careMode ? 17 : 14,
    axisFontSize: careMode ? 16 : 13,
    lineWidth: careMode ? 4 : 3,
    symbolSize: careMode ? 10 : 8,
    careMode: Boolean(careMode),
  };
}

export function buildTrendChartOption({
  theme = "light",
  careMode = false,
  accent = "user",
  xAxisData = [],
  yAxisData = [],
  unit = "",
  referenceLines = [],
} = {}) {
  const appearance = resolveTrendChartAppearance({ theme, careMode, accent });
  const axisLabel = {
    color: appearance.secondaryText,
    fontFamily: appearance.fontFamily,
    fontSize: appearance.axisFontSize,
    hideOverlap: true,
  };
  const styledReferenceLines = referenceLines.map((line) => ({
    ...line,
    label: {
      show: false,
      color: appearance.referenceLine,
      fontFamily: appearance.fontFamily,
      fontSize: appearance.axisFontSize,
      ...(line.label || {}),
    },
    lineStyle: {
      color: appearance.referenceLine,
      type: "dashed",
      width: appearance.careMode ? 2 : 1,
      ...(line.lineStyle || {}),
    },
  }));

  return {
    // Component-level additions (such as markArea) reuse the exact palette
    // resolved here so light/dark/care modes cannot drift apart.
    __appearance: appearance,
    backgroundColor: "transparent",
    textStyle: {
      color: appearance.text,
      fontFamily: appearance.fontFamily,
      fontSize: appearance.fontSize,
    },
    tooltip: {
      trigger: "axis",
      confine: true,
      backgroundColor: appearance.tooltipBackground,
      borderColor: appearance.tooltipBorder,
      borderWidth: 1,
      padding: appearance.careMode ? [12, 14] : [9, 12],
      textStyle: {
        color: appearance.text,
        fontFamily: appearance.fontFamily,
        fontSize: appearance.fontSize,
      },
      axisPointer: {
        lineStyle: { color: appearance.axis, type: "dashed" },
      },
      extraCssText: `box-shadow: ${appearance.tooltipShadow}; border-radius: 10px;`,
    },
    grid: {
      left: appearance.careMode ? 20 : 16,
      right: appearance.careMode ? 28 : 22,
      top: appearance.careMode ? 48 : 40,
      bottom: appearance.careMode ? 24 : 18,
      containLabel: true,
    },
    xAxis: {
      type: "category",
      data: xAxisData,
      boundaryGap: false,
      axisLine: { lineStyle: { color: appearance.axis } },
      axisTick: { lineStyle: { color: appearance.axis } },
      axisLabel: { ...axisLabel, margin: appearance.careMode ? 14 : 10 },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      name: unit,
      nameLocation: "end",
      nameGap: appearance.careMode ? 18 : 14,
      nameTextStyle: {
        color: appearance.secondaryText,
        fontFamily: appearance.fontFamily,
        fontSize: appearance.axisFontSize,
        padding: [0, 0, 0, 8],
      },
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel,
      splitLine: {
        show: true,
        lineStyle: { color: appearance.grid, type: "dashed" },
      },
    },
    series: [
      {
        type: "line",
        smooth: true,
        data: yAxisData,
        connectNulls: false,
        symbolSize: appearance.symbolSize,
        itemStyle: { color: appearance.accent },
        lineStyle: { color: appearance.accent, width: appearance.lineWidth },
        // Keep the measured series visually separate from the reference band.
        // The line and symbols encode observations; the low-saturation band
        // encodes the standard range.
        areaStyle: undefined,
        emphasis: { focus: "series" },
        markLine: styledReferenceLines.length
          ? { symbol: "none", silent: true, data: styledReferenceLines }
          : undefined,
      },
    ],
  };
}
