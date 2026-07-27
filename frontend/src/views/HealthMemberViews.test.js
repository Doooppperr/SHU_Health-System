import { flushPromises, mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import HealthDataView from "./HealthDataView.vue";
import HealthTimelineView from "./HealthTimelineView.vue";
import TrendView from "./TrendView.vue";
import { useAuthStore } from "../stores/auth";

const mocks = vi.hoisted(() => ({
  route: { query: {} },
  router: { push: vi.fn(), replace: vi.fn().mockResolvedValue() },
  fetchFriends: vi.fn(),
  fetchHealthData: vi.fn(),
  fetchHealthDomains: vi.fn(),
  fetchHealthTrends: vi.fn(),
  fetchTimeline: vi.fn(),
  fetchInstitutions: vi.fn(),
  streamAiTrendAnalysis: vi.fn(),
}));

vi.mock("vue-router", () => ({ useRoute: () => mocks.route, useRouter: () => mocks.router }));
vi.mock("../api/friends", () => ({ fetchFriends: mocks.fetchFriends }));
vi.mock("../api/health", () => ({
  fetchHealthData: mocks.fetchHealthData,
  fetchHealthDomains: mocks.fetchHealthDomains,
  fetchHealthTrends: mocks.fetchHealthTrends,
  fetchTimeline: mocks.fetchTimeline,
}));
vi.mock("../api/institutions", () => ({ fetchInstitutions: mocks.fetchInstitutions }));
vi.mock("../api/ai", () => ({ streamAiTrendAnalysis: mocks.streamAiTrendAnalysis }));

const friendPayload = {
  outgoing: [{ auth_status: true, relation_name: "父亲", friend_user: { id: 12, display_name: "亲友姓名" } }],
  incoming: [],
};
const domainPayload = { items: [{ id: 1, code: "basic", name: "基础体征与体格" }] };
const wrappers = [];

function mountView(component, query = {}) {
  mocks.route.query = query;
  const pinia = createPinia();
  setActivePinia(pinia);
  useAuthStore(pinia).user = { id: 1, username: "本人账号", role: "user" };
  const wrapper = mount(component, {
    global: {
      plugins: [pinia, ElementPlus],
      stubs: { HealthTrendChart: { template: '<div data-testid="trend-chart" />' } },
    },
  });
  wrappers.push(wrapper);
  return wrapper;
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.router.replace.mockResolvedValue();
  mocks.fetchFriends.mockResolvedValue({ data: friendPayload });
  mocks.fetchHealthDomains.mockResolvedValue({ data: domainPayload });
  mocks.fetchInstitutions.mockResolvedValue({ data: { items: [] } });
  mocks.fetchHealthData.mockResolvedValue({ data: { owner: { id: 12 }, items: [], pagination: { page: 1, page_size: 15, total: 0 } } });
  mocks.fetchTimeline.mockResolvedValue({ data: { owner: { id: 12 }, items: [], pagination: { page: 1, page_size: 15, total: 0 } } });
  mocks.fetchHealthTrends.mockResolvedValue({
    data: {
      owner: { id: 12 },
      series_by_indicator: [],
      source_options: [{ value: "all", label: "全部来源" }],
    },
  });
});

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
});

describe("健康成员筛选请求", () => {
  it("体检数据选择亲友时发送亲友编号", async () => {
    mountView(HealthDataView, { owner_id: "12" });
    await flushPromises();
    expect(mocks.fetchHealthData).toHaveBeenCalledWith(expect.objectContaining({ owner_id: 12 }));
  });

  it("健康时间线选择亲友时发送亲友编号", async () => {
    mountView(HealthTimelineView, { owner_id: "12" });
    await flushPromises();
    expect(mocks.fetchTimeline).toHaveBeenCalledWith(expect.objectContaining({ owner_id: 12 }));
  });

  it("健康趋势和 AI 使用相同的亲友编号", async () => {
    mountView(TrendView, { owner_id: "12", domain_id: "1" });
    await flushPromises();
    expect(mocks.fetchHealthTrends).toHaveBeenCalledWith(1, expect.objectContaining({ owner_id: 12, source_type: "all" }));
  });

  it("时间线和趋势页不再残留测量入口", async () => {
    const timeline = mountView(HealthTimelineView);
    const trend = mountView(TrendView, { domain_id: "1" });
    await flushPromises();
    expect(timeline.text()).not.toContain("记录今日测量");
    expect(timeline.text()).not.toContain("记录测量");
    expect(trend.text()).not.toContain("记录新测量");
    expect(trend.text()).not.toContain("开始记录");
  });

  it("没有有效参考范围时不显示描述性或暂无范围提示卡", async () => {
    mocks.fetchHealthTrends.mockResolvedValue({
      data: {
        series_by_indicator: [{
          indicator: { id: 1, name: "身高", unit: "cm" },
          points: [{ value: 175, measured_at: "2026-07-24" }],
          summary: { latest: 175, change: 0 },
          reference: {
            label: "描述性测量值",
            context: "该数值不单独判定正常或异常",
          },
        }],
        source_options: [{ value: "all", label: "全部来源" }],
      },
    });
    const wrapper = mountView(TrendView, { domain_id: "1" });
    await flushPromises();
    expect(wrapper.find(".trend-reference-note").exists()).toBe(false);
    expect(wrapper.find(".trend-chart-platform").classes()).not.toContain("has-reference-note");
    expect(wrapper.text()).not.toContain("描述性测量值");
    expect(wrapper.text()).not.toContain("暂无统一参考范围");
  });

  it("有范围时只展示简洁的参考范围标题和数值", async () => {
    mocks.fetchHealthTrends.mockResolvedValue({
      data: {
        series_by_indicator: [{
          indicator: { id: 2, name: "体温", unit: "°C" },
          points: [{ value: 36.4, measured_at: "2026-07-24" }],
          summary: { latest: 36.4, change: -0.2 },
          reference: {
            low: 36.1,
            high: 37.2,
            label: "机构报告参考范围",
            context: "优先采用机构报告提供的参考范围",
            source_url: "https://example.test/reference",
          },
        }],
        source_options: [{ value: "all", label: "全部来源" }],
      },
    });
    const wrapper = mountView(TrendView, { domain_id: "1" });
    await flushPromises();
    const note = wrapper.get(".trend-reference-note");
    expect(wrapper.find(".trend-chart-platform").classes()).toContain("has-reference-note");
    expect(note.text()).toContain("参考范围");
    expect(note.text()).toContain("36.1–37.2 °C");
    expect(note.text()).not.toContain("机构报告参考范围");
    expect(note.text()).not.toContain("优先采用机构报告提供的参考范围");
    expect(note.find("a").exists()).toBe(false);
  });
});
