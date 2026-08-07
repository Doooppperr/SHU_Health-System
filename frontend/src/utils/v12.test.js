import { describe, expect, it } from "vitest";

import {
  appointmentProgress,
  businessDateString,
  bookingDateBounds,
  collectAbnormalTrendItems,
  isBasicProfileComplete,
  isBookingDateDisabled,
} from "./v12";

describe("v12 user workflow helpers", () => {
  it("treats identity as complete only after all locked fields exist", () => {
    expect(isBasicProfileComplete({ real_name: "林晓晨", gender: "female", birth_date: "1990-01-02" })).toBe(true);
    expect(isBasicProfileComplete({ real_name: "林晓晨", gender: "female" })).toBe(false);
    expect(isBasicProfileComplete({ profile_completed: false, real_name: "林晓晨", gender: "female", birth_date: "1990-01-02" })).toBe(false);
    expect(isBasicProfileComplete({ identity_completed: false, profile_completed: true })).toBe(false);
    expect(isBasicProfileComplete({ identity_completed: true })).toBe(true);
  });

  it("opens booking from tomorrow through day 30", () => {
    // 2026-07-30 16:30 in Shanghai, independent of the test runner's host timezone.
    const now = new Date("2026-07-30T08:30:00.000Z");
    const bounds = bookingDateBounds(now);
    expect(bounds.minString).toBe("2026-07-31");
    expect(bounds.maxString).toBe("2026-08-29");
    expect(isBookingDateDisabled(new Date(2026, 6, 30), now)).toBe(true);
    expect(isBookingDateDisabled(new Date(2026, 6, 31), now)).toBe(false);
    expect(isBookingDateDisabled(new Date(2026, 7, 30), now)).toBe(true);
  });

  it("uses the Shanghai business date across the UTC midnight boundary", () => {
    const beforeShanghaiMidnight = new Date("2026-07-30T15:59:59.000Z");
    const afterShanghaiMidnight = new Date("2026-07-30T16:00:00.000Z");

    expect(businessDateString(beforeShanghaiMidnight)).toBe("2026-07-30");
    expect(bookingDateBounds(beforeShanghaiMidnight).minString).toBe("2026-07-31");
    expect(businessDateString(afterShanghaiMidnight)).toBe("2026-07-31");
    expect(bookingDateBounds(afterShanghaiMidnight).minString).toBe("2026-08-01");
  });

  it("collects abnormal points with domain, direction and source", () => {
    const items = collectAbnormalTrendItems([{
      indicator: { id: 2, name: "收缩压", unit: "mmHg" },
      reference: { low: 90, high: 139 },
      points: [
        {
          date: "2026-07-28",
          value: 146,
          result_status: "high",
          health_data_id: "hd-i-18",
          source: { name: "澄心健康", branch_name: "徐汇院区" },
        },
        {
          date: "2026-07-29",
          value: 132,
          result_status: "normal",
          is_abnormal: false,
          source: { type: "self", name: "个人日常测量" },
        },
      ],
    }], { name: "心脑血管" });
    expect(items).toEqual([expect.objectContaining({
      domain: "心脑血管",
      indicator: "收缩压",
      direction: "偏高",
      source: "澄心健康 · 徐汇院区",
      reference: "90–139 mmHg",
      detailId: "hd-i-18",
      latestRecovered: true,
    })]);
  });

  it("does not mark an older abnormal result as recovered while the latest result remains abnormal", () => {
    const items = collectAbnormalTrendItems([{
      indicator: { id: 3, name: "空腹血糖", unit: "mmol/L" },
      points: [
        { date: "2026-07-28", value: 7.1, result_status: "high" },
        { date: "2026-07-30", value: 7.4, result_status: "high" },
      ],
    }], { name: "代谢健康" });

    expect(items).toHaveLength(2);
    expect(items.every((item) => item.latestRecovered === false)).toBe(true);
  });

  it("treats a latest negative qualitative result as recovery from a historical positive result", () => {
    const items = collectAbnormalTrendItems([{
      indicator: { id: 4, name: "尿蛋白" },
      points: [
        { date: "2026-07-28", value: "阳性", result_status: "positive" },
        { date: "2026-07-30", value: "阴性", result_status: "negative" },
      ],
    }], { name: "泌尿健康" });

    expect(items).toEqual([
      expect.objectContaining({ direction: "阳性", latestRecovered: true }),
    ]);
  });

  it("does not claim recovery when the latest point has no explicit normal status", () => {
    const items = collectAbnormalTrendItems([{
      indicator: { id: 5, name: "影像结论" },
      points: [
        { date: "2026-07-28", value: "异常影", result_status: "abnormal" },
        { date: "2026-07-30", value: "待复核", result_status: "unknown", is_abnormal: false },
      ],
    }], { name: "影像检查" });

    expect(items).toEqual([
      expect.objectContaining({ direction: "异常", latestRecovered: false }),
    ]);
  });

  it("marks the report review stage before final archive", () => {
    const progress = appointmentProgress({
      status: "awaiting_report",
      report_status: "pending_review",
    });
    expect(progress.steps.map((step) => step.state)).toEqual([
      "done",
      "done",
      "done",
      "current",
      "pending",
    ]);
    expect(progress.steps[3].label).toBe("待复核");
  });

  it("shows terminal cancellation as a branch instead of a successful finish", () => {
    const progress = appointmentProgress({
      status: "cancelled",
      created_at: "2026-07-29T02:00:00Z",
      cancelled_at: "2026-07-29T03:00:00Z",
    });
    expect(progress.terminal).toBe("用户已取消");
    expect(progress.steps[0]).toEqual(expect.objectContaining({
      key: "booked",
      state: "done",
      occurred_at: "2026-07-29T02:00:00Z",
    }));
    expect(progress.steps.at(-1)).toEqual(expect.objectContaining({ state: "terminal", label: "用户已取消" }));
  });

  it("uses the masked receipt progress stage and event times", () => {
    const progress = appointmentProgress({
      status: "awaiting_report",
      progress_stage: "pending_review",
      report_status: "pending_review",
      events: [
        { type: "booked", occurred_at: "2026-07-28T01:00:00Z" },
        { type: "pending_review", occurred_at: "2026-07-29T06:00:00Z" },
      ],
    });

    expect(progress.steps[3]).toEqual(expect.objectContaining({
      key: "pending_review",
      state: "current",
      occurred_at: "2026-07-29T06:00:00Z",
    }));
  });
});
