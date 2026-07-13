export const AI_PANEL_MIN_WIDTH = 360;
export const AI_PANEL_MAX_WIDTH = 760;
export const AI_PANEL_COMPACT_BREAKPOINT = 860;
export const AI_ROUTE_DESIGN_WIDTH = 1440;
export const AI_ROUTE_MIN_SCALE = 0.7;

function positiveNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}

export function getAiPanelMaxWidth(viewportWidth) {
  const width = positiveNumber(viewportWidth, AI_PANEL_MIN_WIDTH);
  const preferredMaxWidth = Math.max(
    AI_PANEL_MIN_WIDTH,
    Math.round(Math.min(AI_PANEL_MAX_WIDTH, width * 0.55))
  );
  if (width <= AI_PANEL_COMPACT_BREAKPOINT) {
    return preferredMaxWidth;
  }

  const designWidth = Math.min(width, AI_ROUTE_DESIGN_WIDTH);
  const readableMaxWidth = Math.floor(width - designWidth * AI_ROUTE_MIN_SCALE);
  if (readableMaxWidth < AI_PANEL_MIN_WIDTH) {
    return preferredMaxWidth;
  }
  return Math.max(
    AI_PANEL_MIN_WIDTH,
    Math.min(preferredMaxWidth, readableMaxWidth)
  );
}

export function normalizeAiPanelWidth(panelWidth, viewportWidth) {
  const width = positiveNumber(viewportWidth, AI_PANEL_MIN_WIDTH);
  const requestedWidth = positiveNumber(panelWidth, AI_PANEL_MIN_WIDTH);
  return Math.round(
    Math.min(
      Math.max(AI_PANEL_MIN_WIDTH, requestedWidth),
      getAiPanelMaxWidth(width)
    )
  );
}

export function calculateAiStageLayout({
  active,
  viewportWidth,
  viewportHeight,
  panelWidth,
}) {
  const width = positiveNumber(viewportWidth, AI_PANEL_MIN_WIDTH);
  const height = positiveNumber(viewportHeight, 1);

  if (!active || width <= AI_PANEL_COMPACT_BREAKPOINT) {
    return {
      panelWidth: positiveNumber(panelWidth, AI_PANEL_MIN_WIDTH),
      availableWidth: width,
      designWidth: width,
      designHeight: height,
      scale: 1,
      scaled: false,
      overlay: active,
    };
  }

  const normalizedPanelWidth = normalizeAiPanelWidth(panelWidth, width);
  const availableWidth = Math.max(1, width - normalizedPanelWidth);
  const minimumDesignWidth = Math.min(width, AI_ROUTE_DESIGN_WIDTH);
  const designWidth = Math.max(availableWidth, minimumDesignWidth);
  const scale = Math.min(1, availableWidth / designWidth);

  if (scale < AI_ROUTE_MIN_SCALE) {
    return {
      panelWidth: normalizedPanelWidth,
      availableWidth: width,
      designWidth: width,
      designHeight: height,
      scale: 1,
      scaled: false,
      overlay: true,
    };
  }

  return {
    panelWidth: normalizedPanelWidth,
    availableWidth,
    designWidth,
    designHeight: height / scale,
    scale,
    scaled: scale < 1,
    overlay: false,
  };
}
