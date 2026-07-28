import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import {
  appointmentMeta,
  formatBusinessDate,
  formatDate,
  genderLabel,
  normalizeTimelineEntry,
  packageTypeLabel,
  resultStatusMeta,
  shouldDisplayResultStatus,
} from "./userPlatform";

describe("user platform presentation helpers", () => {
  it("constrains range pickers to their responsive filter column", () => {
    const platformCss = readFileSync(resolve(process.cwd(), "src/user-platform.css"), "utf8");
    expect(platformCss).toContain(".health-data-filter-grid .filter-field .el-date-editor");
    expect(platformCss).toMatch(/\.health-data-filter-grid[\s\S]*?\.el-date-editor[\s\S]*?width:\s*100%\s*!important/);
    expect(platformCss).toMatch(/\.health-data-filter-grid[\s\S]*?\.el-range-input[\s\S]*?min-width:\s*0/);
  });

  it("normalizes the unified exam timeline read model", () => {
    const result = normalizeTimelineEntry({
      record_type: "exam",
      record_key: "exam-8",
      business_date: "2026-07-18",
      health_data_id: "hd-i-8",
      item: { id: 8, status: "fulfilled", package_name: "安心综合体检" },
      events: [{ type: "archived", message: "健康数据已归档" }],
    });

    expect(result.kind).toBe("exam");
    expect(result.detailId).toBe("hd-i-8");
    expect(result.item.package_name).toBe("安心综合体检");
  });

  it("normalizes a daily self record without exposing raw storage fields", () => {
    const result = normalizeTimelineEntry({
      record_type: "self",
      record_key: "hd-s-1-2026-07-20",
      business_date: "2026-07-20",
      health_data_id: "hd-s-1-2026-07-20",
      domains: [{ id: 1, name: "基础体征" }],
      summary: { indicator_count: 4 },
    });

    expect(result.kind).toBe("self");
    expect(result.indicatorCount).toBe(4);
    expect(result.domains[0].name).toBe("基础体征");
  });

  it("turns internal enums into user-facing language", () => {
    expect(appointmentMeta("awaiting_report").label).toBe("待出结果");
    expect(genderLabel("female_all")).toBe("女性");
    expect(packageTypeLabel("combined")).toBe("综合组合");
  });

  it("renders only statuses backed by an actual determination", () => {
    expect(shouldDisplayResultStatus("normal")).toBe(true);
    expect(shouldDisplayResultStatus("high")).toBe(true);
    expect(shouldDisplayResultStatus("unknown")).toBe(false);
    expect(shouldDisplayResultStatus(undefined)).toBe(false);
    expect(resultStatusMeta("unknown").label).toBe("");
  });

  it("never renders the browser Invalid Date marker", () => {
    expect(formatBusinessDate("2026-07-21T08:30:00+08:00", { short: true })).not.toContain("Invalid");
    expect(formatBusinessDate("not-a-date")).toBe("日期待核对");
    expect(formatDate("broken-date")).toBe("日期待核对");
  });

  it("wires timeline tabs to reload and login enter to one form submission", () => {
    const timeline = readFileSync(resolve(process.cwd(), "src/views/HealthTimelineView.vue"), "utf8");
    const login = readFileSync(resolve(process.cwd(), "src/views/LoginView.vue"), "utf8");
    expect(timeline).toMatch(/async function recordTypeChanged\([\s\S]*?await apply\(\)/);
    expect(login).not.toContain('@keyup.enter="onSubmit"');
    expect(login).toContain("if (loading.value) return");
  });
});
