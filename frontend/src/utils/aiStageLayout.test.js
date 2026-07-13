import { describe, expect, it } from "vitest";

import {
  AI_PANEL_COMPACT_BREAKPOINT,
  AI_ROUTE_MIN_SCALE,
  calculateAiStageLayout,
  getAiPanelMaxWidth,
  normalizeAiPanelWidth,
} from "./aiStageLayout";

describe("AI route-stage layout", () => {
  it("keeps the underlying page unchanged while the panel is closed", () => {
    const layout = calculateAiStageLayout({
      active: false,
      viewportWidth: 1600,
      viewportHeight: 900,
      panelWidth: 640,
    });

    expect(layout).toMatchObject({
      availableWidth: 1600,
      designWidth: 1600,
      designHeight: 900,
      scale: 1,
      scaled: false,
      overlay: false,
    });
  });

  it("uses a full-screen overlay through the shared compact breakpoint", () => {
    const layout = calculateAiStageLayout({
      active: true,
      viewportWidth: AI_PANEL_COMPACT_BREAKPOINT,
      viewportHeight: 760,
      panelWidth: 720,
    });

    expect(layout.availableWidth).toBe(AI_PANEL_COMPACT_BREAKPOINT);
    expect(layout.scale).toBe(1);
    expect(layout.scaled).toBe(false);
    expect(layout.overlay).toBe(true);
  });

  it("uses an overlay above the compact breakpoint when scaling would be unreadable", () => {
    const layout = calculateAiStageLayout({
      active: true,
      viewportWidth: AI_PANEL_COMPACT_BREAKPOINT + 1,
      viewportHeight: 700,
      panelWidth: 720,
    });

    expect(layout.panelWidth).toBe(474);
    expect(layout.availableWidth).toBe(861);
    expect(layout.designWidth).toBe(861);
    expect(layout.scale).toBe(1);
    expect(layout.scaled).toBe(false);
    expect(layout.overlay).toBe(true);
  });

  it("scales a desktop design canvas instead of reflowing it", () => {
    const layout = calculateAiStageLayout({
      active: true,
      viewportWidth: 1600,
      viewportHeight: 900,
      panelWidth: 560,
    });

    expect(layout.panelWidth).toBe(560);
    expect(layout.availableWidth).toBe(1040);
    expect(layout.designWidth).toBe(1440);
    expect(layout.scale).toBeCloseTo(1040 / 1440);
    expect(layout.designWidth * layout.scale).toBeCloseTo(layout.availableWidth);
    expect(layout.designHeight * layout.scale).toBeCloseTo(900);
    expect(layout.scale).toBeGreaterThanOrEqual(AI_ROUTE_MIN_SCALE);
    expect(layout.overlay).toBe(false);
  });

  it("does not shrink content when the remaining desktop area is already wide", () => {
    const layout = calculateAiStageLayout({
      active: true,
      viewportWidth: 1920,
      viewportHeight: 1080,
      panelWidth: 360,
    });

    expect(layout.availableWidth).toBe(1560);
    expect(layout.designWidth).toBe(1560);
    expect(layout.scale).toBe(1);
    expect(layout.scaled).toBe(false);
    expect(layout.overlay).toBe(false);
  });

  it("shares the same persisted-width clamp with the assistant panel", () => {
    expect(getAiPanelMaxWidth(900)).toBe(495);
    expect(normalizeAiPanelWidth(720, 900)).toBe(495);
    expect(normalizeAiPanelWidth(100, 1200)).toBe(360);
    expect(getAiPanelMaxWidth(1600)).toBe(592);
  });
});
